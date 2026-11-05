"""Additional live probes; snapshots are evidence only, not historical backfills."""
import inspect
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import akshare as ak
from app.providers.client import Client
root=Path(__file__).resolve().parents[1]
out=root/'data/probes';out.mkdir(parents=True,exist_ok=True)
client=Client(retries=0,timeout=30)
cases=[('stock_zt_pool_previous_em',{'date':'20261104'}),
       ('stock_zh_a_spot_em',{}),('stock_board_concept_name_em',{}),
       ('stock_board_concept_cons_em',{'symbol':'人工智能'}),
       ('stock_lhb_detail_em',{'start_date':'20261104','end_date':'20261104'}),
       ('stock_zt_pool_em',{'date':'20260930'}),('stock_zt_pool_em',{'date':'20261009'})]
results=[]
for endpoint,params in cases:
    start=time.monotonic();item={'provider':'akshare','endpoint':endpoint,'params':params,'signature':str(inspect.signature(getattr(ak,endpoint))),'fetched_at':datetime.now(timezone.utc).isoformat()}
    try:
        payload=client.fetch(endpoint,**params)
        item.update(payload);item['rows']=len(payload['records']);item['status']='success' if payload['records'] else 'empty_unverified'
    except Exception as exc:item.update(status='failed',error=str(exc))
    item['seconds']=round(time.monotonic()-start,2)
    path=out/f'extended_{endpoint}_{params.get("date","snapshot")}.json';path.write_text(json.dumps(item,ensure_ascii=False,indent=2))
    summary={k:v for k,v in item.items() if k!='records'};summary['raw_path']=str(path.relative_to(root));results.append(summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)
(out/'extended_summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
