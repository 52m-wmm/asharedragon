"""v0.2 free history commands; deliberately no implicit six-year execution."""
import argparse
import calendar
import json
from datetime import date
from sqlalchemy import select, text, func
from app.db.session import SessionLocal, engine
from app.models import Job, ReconstructionRun, now
from app.collectors.ingest import new_job
from app.collectors.history import run_month
from app.providers.normalize import stock_code


def main():
    p=argparse.ArgumentParser(description='BaoStock monthly history pilot / resumable backfill')
    p.add_argument('command',choices=['collect','backfill'])
    p.add_argument('--start',required=True,type=date.fromisoformat)
    p.add_argument('--end',required=True,type=date.fromisoformat)
    group=p.add_mutually_exclusive_group()
    group.add_argument('--sample-size',type=int,default=24)
    group.add_argument('--codes',help='Comma-separated canonical codes')
    group.add_argument('--all-stocks',action='store_true')
    p.add_argument('--market',action='store_true',help='Use verified daily all-market endpoint; requires --all-stocks')
    p.add_argument('--warmup-days',type=int,default=60)
    p.add_argument('--force',action='store_true')
    p.add_argument('--enqueue',action='store_true')
    p.add_argument('--execute',action='store_true',help='Execute backfill plan; default prints month ranges only')
    a=p.parse_args()
    if a.start>a.end or a.start<date(2020,1,1):p.error('Invalid date range (2020 onward)')
    if a.market and not a.all_stocks:p.error('--market requires --all-stocks')
    if not 1<=a.warmup_days<=120:p.error('--warmup-days must be 1..120')
    if a.sample_size<1:p.error('sample size must be positive')
    codes=[stock_code(c.strip()) for c in a.codes.split(',')] if a.codes else None
    ranges=[];cursor=a.start
    while cursor<=a.end:
        end=min(a.end,date(cursor.year,cursor.month,calendar.monthrange(cursor.year,cursor.month)[1]))
        ranges.append((cursor,end));cursor=date(cursor.year+1,1,1) if cursor.month==12 else date(cursor.year,cursor.month+1,1)
    if a.command=='collect' and len(ranges)>1:p.error('collect accepts one month; use backfill for a plan')
    if a.command=='backfill' and not a.execute:
        print(json.dumps({'plan':[(str(s),str(e)) for s,e in ranges],'scope':'provider_universe' if a.all_stocks else 'sample','execution':'not_started'},ensure_ascii=False));return
    with engine.connect() as lock, SessionLocal() as db:
        if not a.enqueue and not lock.scalar(text('SELECT pg_try_advisory_lock(68142719)')):p.error('A worker or collector is running; use --enqueue')
        if not a.enqueue and not lock.scalar(text('SELECT pg_try_advisory_lock(68142720)')):p.error('Another history collector is running')
        if len(ranges)>3:
            verified=db.scalar(select(func.count(func.distinct(func.date_trunc('month',ReconstructionRun.start_date)))).join(Job,ReconstructionRun.job_id==Job.job_id).where(ReconstructionRun.status=='ready',Job.status=='success',Job.rows_received>0))
            if verified<3:p.error('At least three successful pilot months required before multi-year execution')
        for start,end in ranges:
            job=new_job(db,'history',start,end,'baostock',sample_size=a.sample_size,all_stocks=a.all_stocks,codes=codes,force=a.force,market_transport=a.market,warmup_days=a.warmup_days)
            if not a.enqueue:
                try:job=run_month(db,job)
                except Exception as exc:
                    db.rollback();job=db.get(Job,job.job_id);job.status='failed';job.error_message=str(exc)[:1000];job.finished_at=now();db.commit()
            print(json.dumps({'job_id':job.job_id,'status':job.status,'rows_inserted':job.rows_inserted,'error':job.error_message},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
