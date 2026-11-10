import hashlib
import json
import logging
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select
from app.models import Raw, Coverage, Stock, Daily, Event, Job, TradingDay, Sentiment, now
from app.providers.client import Client, AK_ENDPOINTS
from app.providers.normalize import event_ak, daily_ak, day, stock_code
from app.services.calendar import sessions, previous_day
from app.services.queries import latest_events, serialize
from app.analytics.metrics import sentiment

log=logging.getLogger(__name__)

def new_job(db,dataset,start,end,provider='akshare',**parameters):
    job=Job(job_id=str(uuid4()),provider=provider,dataset=dataset,start_date=start,end_date=end,parameters=parameters)
    db.add(job); db.commit(); return job

def save_raw(db,provider,endpoint,trade_date,params,payload,status,error=None,job_id=None):
    digest=hashlib.sha256(json.dumps({'params':params,'payload':payload,'status':status},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    existing=db.scalar(select(Raw).where(Raw.provider==provider,Raw.endpoint==endpoint,Raw.trade_date==trade_date,Raw.sha256==digest))
    if existing: return existing
    raw=Raw(provider=provider,endpoint=endpoint,trade_date=trade_date,parameters=params,raw_payload=payload,
            sha256=digest,ingestion_status=status,error_message=error,job_id=job_id)
    db.add(raw); db.flush(); return raw

def coverage(db,provider,dataset,date,status,count=None,error=None):
    item=db.scalar(select(Coverage).where(Coverage.provider==provider,Coverage.dataset==dataset,Coverage.trade_date==date))
    if not item:
        item=Coverage(provider=provider,dataset=dataset,trade_date=date,status=status); db.add(item)
    # Failed refresh never downgrades a usable historical snapshot.
    if item.status!='success' or status=='success':
        item.status=status; item.row_count=count
    item.error_message=error; item.checked_at=now()

def evidence(raw,date):
    return dict(provider=raw.provider,endpoint=raw.endpoint,trade_date=date,raw_id=raw.id,
                fetched_at=raw.fetched_at,collected_at=raw.fetched_at,available_at=None)

def sync_calendar(db,client=None):
    client=client or Client()
    payload=client.fetch('tool_trade_date_hist_sina')
    dates=sorted({day(r['trade_date']) for r in payload['records']})
    if not dates: raise ValueError('交易日历为空')
    raw=save_raw(db,client.name,'tool_trade_date_hist_sina',None,{},payload,'success')
    known=set(db.scalars(select(TradingDay.trade_date)))
    for date in dates:
        if date not in known: db.add(TradingDay(trade_date=date,exchange='SSE',source='akshare:sina',raw_id=raw.id))
    db.commit(); return {'start':dates[0],'end':dates[-1],'sessions':len(dates)}

def normalize_pool(records,kind):
    unique={}
    for row in records:
        code=stock_code(row['代码'])
        factual={k:v for k,v in row.items() if k!='序号'}
        if code in unique and unique[code]!=factual:
            raise ValueError('重复股票代码存在冲突字段，拒绝整个分区')
        unique[code]=factual
    return [event_ak(row,kind) for row in unique.values()]

def ingest_pool(db,client,date,kind,job_id=None,force=False):
    endpoint=AK_ENDPOINTS[kind]
    params={'date':date.strftime('%Y%m%d')}
    if not force:
        done=db.scalar(select(Coverage).where(Coverage.provider==client.name,Coverage.dataset==kind,Coverage.trade_date==date,Coverage.status=='success'))
        if done: return 0,0,None
    payload={}; raw=None
    try:
        payload=client.fetch(endpoint,**params)
        records=payload['records']
        if not records: raise ValueError('空响应：不能确认历史覆盖，也不能当作零事件')
        if not {'代码','名称'}.issubset(payload['columns']): raise ValueError('源字段变化：缺少代码/名称')
        normalized=normalize_pool(records,kind)
        raw=save_raw(db,client.name,endpoint,date,params,payload,'success',job_id=job_id)
        inserted=0
        if not db.scalar(select(Event.id).where(Event.raw_id==raw.id).limit(1)):
            for values in normalized:
                db.add(Event(**values,**evidence(raw,date))); inserted+=1
                code=values['stock_code']
                db.add(Stock(stock_code=code,stock_name=values['stock_name'],industry=values['industry'],exchange=code[-2:],
                             valid_from=date,valid_to=date,**evidence(raw,date)))
        coverage(db,client.name,kind,date,'success',len(normalized))
        db.commit()
        return len(records),inserted,None
    except Exception as exc:
        db.rollback(); error=str(exc)[:900]
        save_raw(db,client.name,endpoint,date,params,payload,'failed',error,job_id)
        coverage(db,client.name,kind,date,'missing',error=error); db.commit()
        return len(payload.get('records',[])),0,error

def recalculate(db,date,provider='akshare'):
    current=latest_events(db,provider,date,date)
    statuses={x.dataset:x.status for x in db.scalars(select(Coverage).where(Coverage.trade_date==date,Coverage.provider==provider))}
    pools={kind:[serialize(e) for e in current if e.limit_type==kind] if statuses.get(kind)=='success' else None for kind in AK_ENDPOINTS}
    prev=previous_day(db,date)
    prev_ok=db.scalar(select(Coverage.id).where(Coverage.trade_date==prev,Coverage.provider==provider,Coverage.dataset=='up',Coverage.status=='success')) if prev else None
    previous=[serialize(e) for e in latest_events(db,provider,prev,prev) if e.limit_type=='up'] if prev_ok else None
    values=sentiment(pools['up'],pools['broken'],pools['down'],previous)
    obj=db.get(Sentiment,(date,provider))
    if not obj: obj=Sentiment(trade_date=date,provider=provider); db.add(obj)
    for key,value in values.items(): setattr(obj,key,value)
    obj.calculated_at=now(); db.commit()

def ingest_daily(db,client,code,start,end,job_id=None):
    code=stock_code(code)
    params={'symbol':code[:6],'period':'daily','start_date':start.strftime('%Y%m%d'),'end_date':end.strftime('%Y%m%d'),'adjust':''}
    endpoint='stock_zh_a_hist'; payload={}
    try:
        payload=client.fetch(endpoint,**params)
        if not payload['records']: raise ValueError('日线为空：可能停牌、未上市或数据源缺口，尚未区分')
        normalized=[daily_ak(r,code) for r in payload['records']]
        if any(not start<=r['trade_date']<=end for r in normalized): raise ValueError('返回日期超出请求区间')
        if len({r['trade_date'] for r in normalized})!=len(normalized): raise ValueError('日线日期重复')
        raw=save_raw(db,client.name,endpoint,end,params,payload,'success',job_id=job_id)
        inserted=0
        if not db.scalar(select(Daily.id).where(Daily.raw_id==raw.id).limit(1)):
            for values in normalized:
                date=values.pop('trade_date'); db.add(Daily(**values,**evidence(raw,date))); inserted+=1
        present={day(r['日期']) for r in payload['records']}
        for date in sessions(db,start,end):
            coverage(db,client.name,f'daily:{code}',date,'success' if date in present else 'missing',1 if date in present else None,
                     None if date in present else '无日线记录；停牌/未上市原因未核实')
        db.commit(); return len(normalized),inserted,None
    except Exception as exc:
        db.rollback(); error=str(exc)[:900]
        save_raw(db,client.name,endpoint,end,params,payload,'failed',error,job_id)
        coverage(db,client.name,f'daily:{code}',end,'missing',error=error); db.commit()
        return len(payload.get('records',[])),0,error

def run_job(db,job_id,client=None):
    job=db.get(Job,job_id); client=client or Client(job.provider,timeout=180 if job.provider=='baostock' else None)
    job.status='running'; db.commit(); errors=[]
    try:
        if job.provider=='baostock' and job.dataset=='history':
            from app.collectors.history import run_month
            return run_month(db,job,client if client.name=='baostock' else None)
        if job.provider!='akshare': raise ValueError('Tushare 仅启用原始接口适配，权限/字段验证后方可启用标准化采集')
        if job.dataset=='calendar': sync_calendar(db,client)
        elif job.dataset=='daily':
            sessions(db,job.start_date,job.end_date)
            received,inserted,error=ingest_daily(db,client,job.parameters['stock_code'],job.start_date,job.end_date,job.job_id)
            job.rows_received+=received;job.rows_inserted+=inserted
            if error: errors.append(error)
        elif job.dataset=='pools':
            from app.collectors.repair import pool_kinds
            kinds=pool_kinds(job.parameters.get('kinds'))
            dates=sessions(db,job.start_date,job.end_date)
            for date in dates:
                for kind in kinds:
                    received,inserted,error=ingest_pool(db,client,date,kind,job.job_id,job.parameters.get('force',False))
                    job.rows_received+=received;job.rows_inserted+=inserted
                    if error: errors.append(f'{date} {kind}: {error}')
                    db.commit()
                recalculate(db,date,job.provider)
                # Backfilling yesterday must refresh today's cohort denominator too.
                nxt=db.scalar(select(TradingDay.trade_date).where(TradingDay.trade_date>date).order_by(TradingDay.trade_date).limit(1))
                if nxt and db.get(Sentiment,(nxt,job.provider)): recalculate(db,nxt,job.provider)
                db.commit()
        else: raise ValueError('未知数据集')
        job.status='partial' if errors and job.rows_received else 'failed' if errors else 'success'
        job.error_message='\n'.join(errors)[-6000:] or None
    except Exception as exc:
        db.rollback(); job=db.get(Job,job_id);job.status='failed';job.error_message=str(exc)[:1000]
    job.finished_at=now();db.commit()
    log.info(json.dumps({'event':'job_finished','job_id':job_id,'status':job.status,'inserted':job.rows_inserted}))
    return job
