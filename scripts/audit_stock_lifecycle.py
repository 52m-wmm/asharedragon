"""Fetch public exchange rosters into separate evidence files; never edit price data."""
import json
from datetime import datetime,timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from app.providers.client import Client

TASKS=[('sh_main','stock_info_sh_name_code',{'symbol':'主板A股'}),('sh_star','stock_info_sh_name_code',{'symbol':'科创板'}),('sz_active','stock_info_sz_name_code',{'symbol':'A股列表'}),('sh_delisted','stock_info_sh_delist',{'symbol':'全部'}),('sz_delisted','stock_info_sz_delist',{'symbol':'终止上市公司'})]
def collect():
 root=Path('data/lifecycle_audit')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');root.mkdir(parents=True)
 def fetch(task):
  key,endpoint,params=task
  result=dict(key=key,endpoint=endpoint,params=params,fetched_at=datetime.now(timezone.utc).isoformat())
  try:result.update(status='success',payload=Client(retries=0,interval=0,timeout=30).fetch(endpoint,**params))
  except Exception as e:result.update(status='failed',error=str(e)[:500])
  (root/(key+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
  print(key,result['status'],len(result.get('payload',{}).get('records',[])),flush=True)
  return {k:v for k,v in result.items() if k!='payload'}
 with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(fetch,TASKS))
 (root/'collection.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print('Saved:',root,flush=True)
 return root
def reconcile(root):
 import hashlib,csv
 import pyarrow.parquet as pq
 from app.services.lifecycle_audit import normalize,compare
 catalogue=Path.home()/'Datasets/asharedragon/research/stocks.parquet'
 evidence=[];sources=[]
 for key,endpoint,params in TASKS:
  path=root/(key+'.json');response=json.loads(path.read_text())
  records=response.get('payload',{}).get('records',[])
  accepted=[v for row in records if (v:=normalize(key,row)) is not None]
  sources.append(dict(key=key,status=response['status'],received=len(records),accepted=len(accepted),excluded=len(records)-len(accepted),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),fetched_at=response['fetched_at']))
  evidence.extend(accepted)
 result=compare(pq.read_table(catalogue).to_pylist(),evidence)
 result.update(checked_at=datetime.now(timezone.utc).isoformat(),sources=sources,catalogue_sha256=hashlib.sha256(catalogue.read_bytes()).hexdigest(),
   notes=['仅核对上市、退市日期；不代表行情或停复牌已经核验。','沪市退市响应的“暂停上市日期”列对应 AKShare 源码中的交易所 DELIST_DATE。','当前在市名单不能排除历史重新上市；冲突和缺失均不自动覆盖。','沪市退市接口返回公司代码，部分 A/B 股共用公司代码；多日期冲突不自动选择。','退市日期差一日可能是最后交易日与摘牌日口径不同，需逐项核实。','本次名单不含北交所；名单无匹配不等于本地数据错误。'])
 (root/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
 with (root/'differences.csv').open('w') as f:
  fields=['stock_code','name','status','local_listing','local_delisting','exchange_listing','exchange_delisting','reasons']
  writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(r for r in result['rows'] if r['status']!='verified')
 pointer=Path('data/lifecycle_audit/latest.json');tmp=pointer.with_suffix('.tmp');tmp.write_text(json.dumps({'dataset':root.name}));tmp.replace(pointer)
 print(result['counts'],flush=True)
 return result

if __name__=='__main__':
 import sys
 reconcile(Path(sys.argv[1]) if len(sys.argv)>1 else collect())
