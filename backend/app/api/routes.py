from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models import Coverage, Sentiment, Job, Raw, StockTheme, Theme, DragonLabel, TradingDay, Event
from app.schemas import JobRequest
from app.services.queries import serialize, latest_events, latest_daily
from app.services.calendar import sessions, completed_date
from app.providers.normalize import stock_code
from app.collectors.ingest import new_job
from app.core.config import settings

router=APIRouter(prefix='/api')
def ok(data,**meta): return {'data':data,'meta':{'timezone':'Asia/Shanghai',**meta},'error':None}

@router.get('/health')
def health(db:Session=Depends(get_db)):
    db.execute(text('SELECT 1'));return ok({'status':'ok','database':'postgresql'})

@router.get('/dashboard')
def dashboard(provider:Literal['akshare','tushare','baostock']='akshare',db:Session=Depends(get_db)):
    rows=list(db.scalars(select(Sentiment).where(Sentiment.provider==provider).order_by(Sentiment.trade_date.desc()).limit(90)))
    latest=db.scalar(select(func.max(Coverage.trade_date)).where(Coverage.provider==provider,Coverage.dataset=='up',Coverage.status=='success'))
    latest_completed=db.scalar(select(func.max(TradingDay.trade_date)).where(TradingDay.trade_date<=completed_date()))
    expected=list(db.scalars(select(TradingDay.trade_date).where(TradingDay.trade_date<=completed_date()).order_by(TradingDay.trade_date.desc()).limit(90)))
    observed={r.trade_date:serialize(r) for r in rows}
    history=[observed.get(d,{'trade_date':d,'limit_up_count':None,'broken_limit_rate':None,'max_consecutive_boards':None}) for d in reversed(expected)]
    return ok({'latest_trade_date':latest,'latest_completed_trade_date':latest_completed,
               'current':serialize(db.get(Sentiment,(latest,provider))) if latest and db.get(Sentiment,(latest,provider)) else None,
               'history':history},scope='供应商股票池；缺失不是零',provider=provider)

@router.get('/events')
def events(trade_date:date,provider:Literal['akshare','tushare','baostock']='akshare',
           kind:Literal['up','broken','down']='up',purpose:Literal['trading','history']='trading',search:str='',industry:str='',theme:str='',
           min_boards:int|None=Query(None,ge=1),min_cap:float|None=None,max_cap:float|None=None,
           min_turnover:float|None=None,max_pe:float|None=None,min_amount:float|None=None,
           sort:Literal['consecutive_limit_days','amount','turnover_rate','circulating_market_cap','first_limit_time','stock_code']='consecutive_limit_days',
           descending:bool=True,page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=200),db:Session=Depends(get_db)):
    rows=[serialize(x) for x in latest_events(db,provider,trade_date,trade_date) if x.limit_type==kind]
    candidate_meta=None
    if purpose=='trading':
        from app.research.st_research_scope import trading_candidates
        rows,candidate_meta=trading_candidates(rows)
    if search: rows=[x for x in rows if search.lower() in (x['stock_code']+(x['stock_name'] or '')).lower()]
    if industry: rows=[x for x in rows if industry in (x['industry'] or '')]
    if theme:
        codes=set(db.scalars(select(StockTheme.stock_code).join(Theme).where(Theme.theme_name.contains(theme),StockTheme.valid_from<=trade_date,
                (StockTheme.valid_to.is_(None))|(StockTheme.valid_to>=trade_date),StockTheme.provider==provider)))
        rows=[x for x in rows if x['stock_code'] in codes]
    for key,threshold,lower in [('consecutive_limit_days',min_boards,True),('circulating_market_cap',min_cap,True),('circulating_market_cap',max_cap,False),('turnover_rate',min_turnover,True),('pe_ttm',max_pe,False),('amount',min_amount,True)]:
        if threshold is not None: rows=[x for x in rows if x[key] is not None and (float(x[key])>=threshold if lower else float(x[key])<=threshold)]
    present=[x for x in rows if x[sort] is not None]; missing=[x for x in rows if x[sort] is None]
    rows=sorted(present,key=lambda x:(x[sort],x['stock_code']),reverse=descending)+missing
    status=db.scalar(select(Coverage).where(Coverage.provider==provider,Coverage.dataset==kind,Coverage.trade_date==trade_date))
    return ok(rows[(page-1)*page_size:page*page_size],total=len(rows),page=page,page_size=page_size,
              coverage=serialize(status) if status else None,provider=provider,purpose=purpose,candidate_eligibility=candidate_meta)

@router.get('/stocks/{code}')
def stock(code:str,provider:Literal['akshare','tushare','baostock']='akshare',start:date|None=None,end:date|None=None,db:Session=Depends(get_db)):
    code=stock_code(code)
    daily=latest_daily(db,provider,code,start,end)
    events=[serialize(x) for x in latest_events(db,provider,start,end) if x.stock_code==code]
    as_date=end or (daily[-1].trade_date if daily else completed_date())
    themes=list(db.execute(select(Theme.theme_name,StockTheme.valid_from,StockTheme.valid_to).join(StockTheme).where(StockTheme.stock_code==code,
        StockTheme.provider==provider,StockTheme.valid_from<=as_date,(StockTheme.valid_to.is_(None))|(StockTheme.valid_to>=as_date))))
    return ok({'stock_code':code,'daily':[serialize(x) for x in daily],'events':events,'themes':[dict(x._mapping) for x in themes],
        'missing_fields':(['历史 PE/PB'] if provider!='baostock' else [])+['自由流通市值','已核实的公开时间','复权收益序列'],
        'price_basis':'未复权价格；除权除息会影响收益，当前不据此生成后验收益标签'},provider=provider)

@router.get('/research/snapshot')
def snapshot(code:str,trade_date:date,as_of:datetime,mode:Literal['strict','collected']='strict',provider:Literal['akshare','tushare','baostock']='akshare',db:Session=Depends(get_db)):
    if as_of.tzinfo is None: raise ValueError('as_of 必须含时区')
    code=stock_code(code)
    events=[serialize(x) for x in latest_events(db,provider,end=trade_date,as_of=as_of) if x.stock_code==code]
    daily=[serialize(x) for x in latest_daily(db,provider,code,end=trade_date,as_of=as_of)]
    if mode=='strict':
        events=[x for x in events if x['available_at'] is not None and x['available_at']<=as_of]
        daily=[x for x in daily if x['available_at'] is not None and x['available_at']<=as_of]
    return ok({'events':events,'daily':daily},mode=mode,warning='strict 排除无法核实公开时间的记录；collected 仅表示本系统当时已采集，不证明市场公开时间。后验标签不在此接口返回。')

@router.get('/seasonality')
def seasonality(start_year:int=Query(2020,ge=2020,le=2100),end_year:int=Query(2026,ge=2020,le=2100),height:int=Query(4,ge=2,le=30),provider:Literal['akshare','tushare','baostock']='akshare',label_version:str='v1',db:Session=Depends(get_db)):
    if start_year>end_year: raise ValueError('年份范围错误')
    start,end=date(start_year,1,1),date(end_year,12,31)
    # Include earlier history to locate actual episode starts, never peak months.
    ev=[e for e in latest_events(db,provider,end=end) if e.limit_type=='up']
    lookup={(e.stock_code,e.trade_date):e for e in ev}
    calendar=list(db.scalars(select(TradingDay.trade_date).where(TradingDay.trade_date<=end).order_by(TradingDay.trade_date)))
    positions={d:i for i,d in enumerate(calendar)}
    covered=list(db.scalars(select(Coverage.trade_date).where(Coverage.provider==provider,Coverage.dataset=='up',Coverage.status=='success',Coverage.trade_date.between(start,end))))
    coverage_counts=defaultdict(int)
    for d in covered: coverage_counts[(d.year,d.month)]+=1
    stats=defaultdict(lambda:{'first_boards':0,'high_board_starts':0,'max_height':None,'dragon_starts':0,'dragon_mean_gain_20d':None})
    episodes=set(); unresolved=0
    for e in ev:
        if e.trade_date>=start:
            cell=stats[(e.trade_date.year,e.trade_date.month)]
            if e.consecutive_limit_days==1: cell['first_boards']+=1
            if e.consecutive_limit_days is not None: cell['max_height']=max(cell['max_height'] or 0,e.consecutive_limit_days)
        n=e.consecutive_limit_days
        if n is None or n<height: continue
        pos=positions.get(e.trade_date)
        if pos is None or pos<n-1: unresolved+=1;continue
        dates=calendar[pos-n+1:pos+1]
        if not all((e.stock_code,d) in lookup and lookup[(e.stock_code,d)].consecutive_limit_days==i+1 for i,d in enumerate(dates)):
            unresolved+=1;continue
        episodes.add((e.stock_code,dates[0]))
    for _,d in episodes:
        if start<=d<=end: stats[(d.year,d.month)]['high_board_starts']+=1
    labels=list(db.scalars(select(DragonLabel).where(DragonLabel.provider==provider,DragonLabel.label_version==label_version,DragonLabel.start_date.between(start,end))))
    gains=defaultdict(list)
    # Multiple dragon types on one episode must not inflate start counts.
    seen=set()
    for label in labels:
        key=(label.stock_code,label.start_date)
        if key in seen: continue
        seen.add(key);cell=(label.start_date.year,label.start_date.month);stats[cell]['dragon_starts']+=1
        if label.max_gain_20d is not None:gains[cell].append(label.max_gain_20d)
    rows=[]
    for year in range(start_year,end_year+1):
        for month in range(1,13):
            key=(year,month); cell=stats[key]
            if not coverage_counts[key]:
                for field in ('first_boards','high_board_starts','max_height'): cell[field]=None
            cell['dragon_starts']=cell['dragon_starts'] if labels else None
            if gains[key]:cell['dragon_mean_gain_20d']=sum(gains[key])/len(gains[key])
            rows.append({'year':year,'month':month,**cell,'covered_sessions':coverage_counts[key]})
    return ok(rows,unresolved_high_board_observations=unresolved,label_version=label_version,
              label_count=len(seen),warning='统计仅基于已覆盖样本；高连板按可完整追溯的首板月份去重。最高高度按观测月份统计。未覆盖月份为 NULL。')

@router.get('/data/status')
def status(start:date|None=None,end:date|None=None,provider:Literal['akshare','tushare','baostock']='akshare',db:Session=Depends(get_db)):
    end=end or completed_date();start=start or end-timedelta(days=90)
    if start>end or (end-start).days>3660: raise ValueError('覆盖查询区间无效')
    coverage=list(db.scalars(select(Coverage).where(Coverage.provider==provider,Coverage.trade_date.between(start,end)).order_by(Coverage.trade_date.desc())))
    expected=list(db.scalars(select(TradingDay.trade_date).where(TradingDay.trade_date.between(start,end))))
    complete={(r.trade_date,r.dataset) for r in coverage if r.status=='success'}
    missing=[{'trade_date':d,'datasets':[k for k in ('up','broken','down') if (d,k) not in complete]} for d in expected]
    missing=[x for x in missing if x['datasets']]
    jobs=list(db.scalars(select(Job).order_by(Job.started_at.desc()).limit(50)))
    failures=list(db.scalars(select(Raw).where(Raw.ingestion_status=='failed').order_by(Raw.fetched_at.desc()).limit(15)))
    bounds=db.execute(select(func.min(Coverage.trade_date),func.max(Coverage.trade_date)).where(Coverage.provider==provider,Coverage.dataset=='up',Coverage.status=='success')).one()
    return ok({'provider':provider,'tushare_configured':bool(settings.tushare_token),'coverage_start':bounds[0],'coverage_end':bounds[1],
               'coverage':[serialize(x) for x in coverage],'missing_dates':missing,
               'jobs':[serialize(x) for x in jobs],
               'errors':[{k:v for k,v in serialize(x).items() if k!='raw_payload'} for x in failures],
               'calendar_bounds':db.execute(select(func.min(TradingDay.trade_date),func.max(TradingDay.trade_date))).one()._asdict()},
              query_start=start,query_end=end,calendar_sessions=len(expected))

@router.post('/jobs',status_code=202)
def enqueue(request:JobRequest,db:Session=Depends(get_db)):
    if request.dataset!='calendar':sessions(db,request.start_date,request.end_date)
    job=new_job(db,request.dataset,request.start_date,request.end_date,request.provider,stock_code=request.stock_code,force=request.force,**({'sample_size':request.sample_size,'all_stocks':request.all_stocks} if request.dataset=='history' else {}))
    return ok(serialize(job))

@router.post('/jobs/{job_id}/retry',status_code=202)
def retry(job_id:str,db:Session=Depends(get_db)):
    job=db.get(Job,job_id)
    if not job:raise HTTPException(404,'任务不存在')
    if job.status not in ('failed','partial','interrupted'):raise HTTPException(409,'仅能重试失败、部分成功或中断的任务')
    return ok(serialize(new_job(db,job.dataset,job.start_date,job.end_date,job.provider,**job.parameters)))

@router.get('/jobs/{job_id}')
def get_job(job_id:str,db:Session=Depends(get_db)):
    job=db.get(Job,job_id)
    if not job:raise HTTPException(404,'任务不存在')
    return ok(serialize(job))
