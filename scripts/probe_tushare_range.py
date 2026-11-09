"""One bounded range query, compared against the complete archived December baseline."""
import json
from collections import Counter
from datetime import date
from pathlib import Path
from sqlalchemy import select,text
from app.db.session import SessionLocal,engine
from app.models import ProxyPartition,Raw
from app.providers.tushare_proxy import ProxyClient,digest,ProxyError
from app.services.calendar import sessions
from archive_tushare import atomic,ROOT

def key(row):return (row['trade_date'],row['ts_code'],row['limit'])
def main():
    c=ProxyClient(accept_http=True,max_requests=3,overrides={'limit_list_d':{'interval':3}})
    result={'range':['20201201','20201231'],'limit_type_omitted':True,'status':'pending'}
    with engine.connect() as lock,SessionLocal() as db:
        if not lock.scalar(text('SELECT pg_try_advisory_lock(68142721)')):raise RuntimeError('collector already running')
        expected={}
        for p in db.scalars(select(ProxyPartition).where(ProxyPartition.source_id==c.source_id,ProxyPartition.endpoint=='limit_list_d',ProxyPartition.trade_date.between(date(2020,12,1),date(2020,12,31)),ProxyPartition.status=='success')):
            if set(p.parameters)=={'trade_date'}:
                for r in db.get(Raw,p.raw_id).raw_payload['records']:expected[key(r)]=r
        if len(expected)!=1728 or len({k[0] for k in expected})!=23:raise RuntimeError('baseline incomplete')
        try:
            payload=c.fetch('limit_list_d',start_date='20201201',end_date='20201231')
            got={key(r):r for r in payload['records']}
            diff=[k for k in got.keys()&expected.keys() if digest(got[k])!=digest(expected[k])]
            result.update(rows=len(payload['records']),counts=dict(Counter(r['limit'] for r in payload['records'])),dates=len({k[0] for k in got}),
                          missing=list(expected.keys()-got.keys()),extra=list(got.keys()-expected.keys()),changed=diff,
                          duplicate_rows=len(payload['records'])-len(got),archive=payload['archive_path'],attempts=payload['attempts'])
            result['status']='verified' if not (result['missing'] or result['extra'] or diff or result['duplicate_rows']) and len(got)<2250 else 'failed_completeness'
        except ProxyError as e:result.update(status='failed_request',error=c.safe(str(e)),attempts=e.attempts)
        result['requests']=c.used
        atomic(ROOT/'data/tushare-range-probe.json',result)
        print(json.dumps(result,ensure_ascii=False,default=str),flush=True)
    c.session.close()
if __name__=='__main__':main()
