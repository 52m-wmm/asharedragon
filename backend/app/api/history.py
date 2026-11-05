"""Additive v0.2 endpoints; v0.1 defaults and observed pools remain untouched."""
from collections import defaultdict
from datetime import date
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models import ReconstructionRun, ReconstructionFact, ReconstructionDay, ValidationReport
from app.services.queries import serialize
from app.providers.normalize import stock_code
from app.api.routes import ok

router=APIRouter(prefix='/api/history')

@router.get('/runs')
def runs(db:Session=Depends(get_db)):
    rows=list(db.scalars(select(ReconstructionRun).order_by(ReconstructionRun.created_at.desc()).limit(100)))
    return ok([serialize(r) for r in rows],data_kind='algorithm_reconstructed',provider='baostock',
              warning='覆盖率以历史供应商股票目录为分母；不声称覆盖北交所或整个 A 股。')

@router.get('/summary')
def summary(run_id:str|None=None,start:date=date(2020,1,1),end:date=date(2026,12,31),db:Session=Depends(get_db)):
    if start>end:raise ValueError('Invalid date range')
    stmt=select(ReconstructionRun).where(ReconstructionRun.status=='ready').order_by(ReconstructionRun.created_at.desc())
    if run_id:stmt=stmt.where(ReconstructionRun.run_id==run_id)
    runs=list(db.scalars(stmt));selected={};run_map={r.run_id:r for r in runs}
    # Use a single complete run snapshot per day. Never add repeated samples together.
    for run in runs:
        for d in db.scalars(select(ReconstructionDay).where(ReconstructionDay.run_id==run.run_id,ReconstructionDay.trade_date.between(start,end))):
            if d.trade_date not in selected:selected[d.trade_date]=d
    days=[serialize(d) for _,d in sorted(selected.items())];months=defaultdict(list)
    for d in days:months[str(d['trade_date'])[:7]].append(d)
    monthly=[]
    for month,rows in sorted(months.items()):
        expected=sum(r['expected_stocks'] for r in rows);received=sum(r['received_stocks'] for r in rows)
        maxima=[r['max_consecutive_boards'] for r in rows]
        monthly.append({'month':month,'sample_limit_up_events':sum(r['limit_up_count'] for r in rows if r['limit_up_count'] is not None) if any(r['limit_up_count'] is not None for r in rows) else None,
                        'max_consecutive_boards':max(maxima) if all(v is not None for v in maxima) else None,
                        'covered_sessions':sum(r['received_stocks']>0 for r in rows),'scheduled_sessions':len(rows),'expected_stock_sessions':expected,'received_stock_sessions':received,
                        'coverage_rate':received/expected if expected else None,
                        'quality':'provider_universe_classified' if all(r['quality']=='provider_universe_classified' for r in rows) else 'partial_sample_or_unclassified',
                        'run_ids':sorted({r['run_id'] for r in rows})})
    return ok({'daily':days,'monthly':monthly},data_kind='algorithm_reconstructed',provider='baostock',
              count_basis='已采集且可判定样本；部分覆盖时不是全市场涨停总数',
              snapshot_policy='latest_ready_run_per_day; use run_id for reproducible fixed cohorts')

@router.get('/events')
def events(run_id:str,trade_date:date|None=None,code:str|None=None,page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=200),db:Session=Depends(get_db)):
    run=db.get(ReconstructionRun,run_id)
    if not run:raise HTTPException(404,'重建批次不存在')
    stmt=select(ReconstructionFact).where(ReconstructionFact.run_id==run_id,ReconstructionFact.classification=='up')
    if trade_date:stmt=stmt.where(ReconstructionFact.trade_date==trade_date)
    if code:stmt=stmt.where(ReconstructionFact.stock_code==stock_code(code))
    rows=list(db.scalars(stmt.order_by(ReconstructionFact.trade_date.desc(),ReconstructionFact.stock_code)))
    data=[{**serialize(r),'first_limit_time':None,'last_limit_time':None,'open_times':None,'seal_amount':None,
           'data_source':'baostock','data_kind':'algorithm_reconstructed'} for r in rows[(page-1)*page_size:page*page_size]]
    return ok(data,total=len(rows),page=page,algorithm_version=run.algorithm_version,scope=run.scope)

@router.get('/first-boards')
def first_boards(code:str,run_id:str|None=None,db:Session=Depends(get_db)):
    stmt=select(ReconstructionFact).join(ReconstructionRun).where(ReconstructionFact.stock_code==stock_code(code),
        ReconstructionFact.classification=='up',ReconstructionRun.status=='ready').order_by(ReconstructionRun.created_at.desc(),ReconstructionFact.trade_date)
    if run_id:stmt=stmt.where(ReconstructionFact.run_id==run_id)
    facts=list(db.scalars(stmt));unique={};unknown=0
    for f in facts:
        if f.first_board_date is None:unknown+=1;continue
        unique.setdefault(f.first_board_date,{'first_board_date':f.first_board_date,'run_id':f.run_id,'observed_height':f.consecutive_limit_days})
    return ok(sorted(unique.values(),key=lambda r:r['first_board_date']),unknown_start_observations=unknown,
              data_kind='algorithm_reconstructed',warning='只返回能回溯到明确非涨停前序的首板日期；缺口不猜测。')

@router.get('/validations')
def validations(run_id:str|None=None,db:Session=Depends(get_db)):
    stmt=select(ValidationReport).order_by(ValidationReport.created_at.desc()).limit(200)
    if run_id:stmt=stmt.where(ValidationReport.run_id==run_id)
    return ok([serialize(r) for r in db.scalars(stmt)],reference='AKShare observed limit-up pool',
              warning='准确率仅对共同可判定样本；未覆盖名单单列。差异原因未经证实则保留 unresolved。')
