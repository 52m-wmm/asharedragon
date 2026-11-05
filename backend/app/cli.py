import argparse
import json
from datetime import date
from calendar import monthrange
from app.db.session import SessionLocal
from app.collectors.ingest import new_job, run_job, sync_calendar
from app.services.calendar import completed_date
from app.models import TradingDay
from sqlalchemy import select, func

def main():
    p=argparse.ArgumentParser(description='AShareDragon data tools')
    p.add_argument('command',choices=['calendar','collect','daily','backfill','incremental'])
    p.add_argument('--start',type=date.fromisoformat);p.add_argument('--end',type=date.fromisoformat)
    p.add_argument('--code');p.add_argument('--force',action='store_true');p.add_argument('--enqueue',action='store_true')
    a=p.parse_args()
    with SessionLocal() as db:
        if a.command=='calendar':print(sync_calendar(db));return
        if a.command=='incremental':
            a.start=a.end=db.scalar(select(func.max(TradingDay.trade_date)).where(TradingDay.trade_date<=completed_date()))
        if not a.start or not a.end:p.error('--start and --end required (sync calendar first)')
        ranges=[]
        if a.command=='backfill':
            cursor=a.start
            while cursor<=a.end:
                end=min(a.end,date(cursor.year,cursor.month,monthrange(cursor.year,cursor.month)[1]));ranges.append((cursor,end))
                cursor=date(cursor.year+1,1,1) if cursor.month==12 else date(cursor.year,cursor.month+1,1)
        else:ranges=[(a.start,a.end)]
        if a.command=='daily' and not a.code:p.error('--code required')
        for start,end in ranges:
            job=new_job(db,'daily' if a.command=='daily' else 'pools',start,end,stock_code=a.code,force=a.force)
            if not a.enqueue:job=run_job(db,job.job_id)
            print(json.dumps({'job_id':job.job_id,'status':job.status,'inserted':job.rows_inserted,'error':job.error_message},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
