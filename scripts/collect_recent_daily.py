"""Resume bounded AKShare daily updates using the local stock catalogue."""
import argparse,json,time,threading
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from datetime import date
from pathlib import Path
import duckdb
from app.db.session import SessionLocal
from sqlalchemy import select
from app.models import Raw,Event
from app.collectors.ingest import ingest_daily
from app.providers.client import Client
from app.services.calendar import sessions


def collect(start,end):
 with SessionLocal() as db:sessions(db,start,end)
 root=Path('data/recent_daily')/f'{start}_{end}';root.mkdir(parents=True,exist_ok=True)
 path=root/'progress.json';report=json.loads(path.read_text()) if path.exists() else {}
 with duckdb.connect() as db:
  codes=[r[0] for r in db.execute("SELECT stock_code FROM read_parquet(?) WHERE (stock_code LIKE '%.SH' OR stock_code LIKE '%.SZ') AND (listing_date IS NULL OR listing_date<=?) AND (delisting_date IS NULL OR delisting_date>=?) ORDER BY stock_code",[str(Path.home()/'Datasets/asharedragon/research/stocks.parquet'),end,start]).fetchall()]
 with SessionLocal() as db:
  priority=set(db.scalars(select(Event.stock_code).where(Event.trade_date.between(start,end))))
 codes.sort(key=lambda code:(code not in priority,code))
 limiter=threading.Lock();last=[0.]
 def fetch(code):
  with limiter:
   time.sleep(max(0,last[0]+.5-time.monotonic()));last[0]=time.monotonic()
  with SessionLocal() as db:
   cached=db.scalar(select(Raw).where(Raw.provider=='akshare',Raw.endpoint=='stock_zh_a_hist',Raw.ingestion_status=='success',Raw.parameters['symbol'].as_string()==code[:6],Raw.parameters['start_date'].as_string()==start.strftime('%Y%m%d'),Raw.parameters['end_date'].as_string()==end.strftime('%Y%m%d'),Raw.parameters['adjust'].as_string()=='').order_by(Raw.id.desc()).limit(1))
   client=Client('akshare',retries=0,interval=0,timeout=25)
   if cached:
    class Replay:
     name='akshare'
     def fetch(self,*args,**kwargs):return cached.raw_payload
    client=Replay()
   received,inserted,error=ingest_daily(db,client,code,start,end)
  return dict(status='failed' if error else 'success',received=received,inserted=inserted,error=error)
 def save():
  tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2));tmp.replace(path)
 pending=iter(c for c in codes if report.get(c,{}).get('status')!='success');failed=0;stop=False
 with ThreadPoolExecutor(max_workers=4) as pool:
  running={}
  for _ in range(4):
   code=next(pending,None)
   if code:running[pool.submit(fetch,code)]=code
  while running:
   done,_=wait(running,return_when=FIRST_COMPLETED)
   for f in done:
    code=running.pop(f)
    try:report[code]=f.result()
    except Exception as e:report[code]=dict(status='failed',error=str(e)[:300])
    failed=failed+1 if report[code]['status']=='failed' else 0
    if failed>=8:stop=True
    if not stop:
     code=next(pending,None)
     if code:running[pool.submit(fetch,code)]=code
   save()
   if len(report)%25<4 or stop:print(json.dumps(dict(done=len(report),total=len(codes),success=sum(r['status']=='success' for r in report.values()),stopped=stop)),flush=True)
 print('Finished',path,flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--start',required=True,type=date.fromisoformat);p.add_argument('--end',required=True,type=date.fromisoformat);a=p.parse_args()
 if (a.end-a.start).days>7:raise ValueError('At most eight calendar days per update')
 collect(a.start,a.end)
