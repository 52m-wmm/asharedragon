"""Bounded real BaoStock probes, executed before production integration."""
import inspect
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

FIELDS = 'date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,isST'
ROOT = Path(__file__).resolve().parents[1]

def child(endpoint, params):
    import baostock as bs
    import contextlib
    import importlib.metadata
    with contextlib.redirect_stdout(sys.stderr):
        login = bs.login()
        if login.error_code != '0':
            raise RuntimeError(f'login {login.error_code}: {login.error_msg}')
        fn = getattr(bs, endpoint)
        result = fn(**params)
        if result.error_code != '0':
            raise RuntimeError(f'{result.error_code}: {result.error_msg}')
        rows = []
        while result.next():
            rows.append(dict(zip(result.fields, result.get_row_data())))
        if result.error_code != '0':
            raise RuntimeError(f'pagination {result.error_code}: {result.error_msg}')
        bs.logout()
    print(json.dumps({'version':importlib.metadata.version('baostock'),'signature':str(inspect.signature(fn)),
                      'columns':result.fields,'records':rows},ensure_ascii=False))

if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1]=='child':
        child(sys.argv[2],json.loads(sys.argv[3]));sys.exit()
    out=ROOT/'data/baostock_probes';out.mkdir(parents=True,exist_ok=True)
    cases=[('query_stock_basic',{}),('query_all_stock',{'day':'2022-01-04'}),
           ('query_trade_dates',{'start_date':'2020-01-01','end_date':'2026-11-04'})]
    cases += [('query_history_k_data_plus',{'code':code,'fields':FIELDS,'start_date':start,'end_date':end,'frequency':'d','adjustflag':'3'})
              for code,start,end in [('sh.600000','2022-01-01','2022-01-31'),('sz.000001','2024-01-01','2024-01-31'),
                                     ('sh.600000','2025-01-01','2025-01-31'),('sz.000003','2020-01-01','2020-01-31')]]
    summaries=[]
    for i,(ep,params) in enumerate(cases):
        t=time.monotonic();record={'provider':'baostock','endpoint':ep,'params':params,'fetched_at':datetime.now(timezone.utc).isoformat()}
        try:
            result=subprocess.run([sys.executable,__file__,'child',ep,json.dumps(params)],capture_output=True,text=True,timeout=35)
            if result.returncode:raise RuntimeError(result.stderr[-1800:])
            payload=json.loads(result.stdout);record.update(payload);record['status']='success' if payload['records'] else 'empty_unverified';record['rows']=len(payload['records'])
        except Exception as exc:record.update(status='failed',error=str(exc))
        record['seconds']=round(time.monotonic()-t,3)
        path=out/f'{i:02}_{ep}.json';path.write_text(json.dumps(record,ensure_ascii=False,indent=2))
        summaries.append({k:v for k,v in record.items() if k!='records'});print(json.dumps(summaries[-1],ensure_ascii=False),flush=True)
        (out/'summary.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2))
        time.sleep(1)
