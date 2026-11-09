"""v0.3 daily/monthly proxy batches. Multi-year work is plan-only by default."""
import argparse
import calendar
import json
from datetime import date
from sqlalchemy import text,select
from app.db.session import SessionLocal,engine
from app.models import ProxyBatch
from app.providers.tushare_proxy import ProxyClient
from app.collectors.tushare_history import ENDPOINTS,new_batch,run_batch
from app.services.calendar import completed_date


def month_ranges(start,end):
    while start<=end:
        stop=min(end,date(start.year,start.month,calendar.monthrange(start.year,start.month)[1]))
        yield start,stop
        start=date(start.year+1,1,1) if start.month==12 else date(start.year,start.month+1,1)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['collect','resume','plan','backfill'])
    p.add_argument('--start',type=date.fromisoformat);p.add_argument('--end',type=date.fromisoformat)
    p.add_argument('--batch-id');p.add_argument('--endpoints',default='limit_list_d')
    p.add_argument('--codes',help='Explicit canonical stock universe, comma-separated')
    p.add_argument('--accept-insecure-http',action='store_true')
    p.add_argument('--execute',action='store_true');p.add_argument('--refresh',action='store_true')
    p.add_argument('--max-requests',type=int,default=120)
    p.add_argument('--config',help='JSON endpoint overrides: timeout, interval, retries only')
    a=p.parse_args();endpoints=a.endpoints.split(',')
    if any(e not in ENDPOINTS for e in endpoints):p.error('unsupported endpoint')
    if a.max_requests<1:p.error('positive max requests required')
    codes=None
    if a.codes:
        from app.providers.normalize import stock_code
        codes=sorted({stock_code(c.strip()) for c in a.codes.split(',')})
    if a.command!='resume':
        if not a.start or not a.end or a.start>a.end or a.start<date(2020,1,1) or a.end>completed_date():p.error('valid completed 2020+ dates required')
        ranges=list(month_ranges(a.start,a.end))
        if a.command=='collect' and len(ranges)>1:p.error('collect limited to one month')
        if a.command=='plan' or (a.command=='backfill' and not a.execute):
            print(json.dumps({'execution':'not_started','order':endpoints,'months':[(str(s),str(e)) for s,e in ranges]},ensure_ascii=False));return
    overrides={}
    if a.config:
        from pathlib import Path
        overrides=json.loads(Path(a.config).read_text())
        for endpoint,values in overrides.items():
            if endpoint not in ENDPOINTS or set(values)-{'timeout','interval','retries'}:p.error('invalid endpoint override')
            if not isinstance(values.get('retries',1),int) or not 0<=values.get('retries',1)<=3 or values.get('interval',1)<=0 or values.get('timeout',1)<=0:p.error('invalid configuration bounds')
    client=ProxyClient(accept_http=a.accept_insecure_http,overrides=overrides,max_requests=a.max_requests)
    try:
        with engine.connect() as lock,SessionLocal() as db:
            if not lock.scalar(text('SELECT pg_try_advisory_lock(68142721)')):p.error('another proxy collector is running')
            if a.command=='resume':
                batch=db.get(ProxyBatch,a.batch_id)
                if not batch or batch.source_id!=client.source_id:p.error('batch missing or source mismatch')
                print(json.dumps(run_batch(db,client,batch)));return
            if len(ranges)>1:
                verified=list(db.scalars(select(ProxyBatch).where(ProxyBatch.status=='success',ProxyBatch.source_id==client.source_id,
                    ProxyBatch.start_date==date(2020,12,1),ProxyBatch.end_date==date(2020,12,31))))
                if not any('limit_list_d' in b.endpoints and not b.parameters.get('codes') for b in verified):p.error('successful 2020-12 full provider pilot required')
            # Endpoint outer loop guarantees all limit_list_d work precedes theme work.
            for endpoint in endpoints:
                for start,end in ranges:
                    if client.used>=client.max_requests:print('request budget exhausted; resume/re-run to continue');return
                    batch=new_batch(db,client,start,end,[endpoint],{'codes':codes,'refresh':a.refresh})
                    result=run_batch(db,client,batch);print(json.dumps(result),flush=True)
                    if result['status']!='success':
                        print('endpoint halted at incomplete month; other endpoints remain independent',flush=True);break
    finally:client.session.close()

if __name__=='__main__':main()
