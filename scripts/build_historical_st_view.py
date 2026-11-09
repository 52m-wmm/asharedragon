"""Persist an independent ST attribute sidecar, without rewriting any source."""
from pathlib import Path
from collections import Counter
import hashlib, json
import pyarrow as pa
import pyarrow.parquet as pq
from app.research.non_st_scope import bundle
from app.research.st_research_scope import status, apply, VERSION

base=Path.home()/'Datasets/asharedragon/research'
root=base/'historical_st_view_v1';root.mkdir(exist_ok=True)
m=json.loads((base/'dragon_non_st_v1/manifest.json').read_text())
data=bundle();days=data['days.json']
rows=[dict(stock_code=r['stock_code'],trade_date=r['trade_date'],historical_st_status=status(r),
           evidence_ids=r['evidence_ids'],conflict=r.get('conflict',False),source_version=m['dataset']) for r in days]
stamp=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()[:20]
out=root/stamp;out.mkdir(exist_ok=True)
if not (out/'historical_st_status.parquet').exists():pq.write_table(pa.Table.from_pylist(rows),out/'historical_st_status.parquet')
parent=base/'dragon_seasonality_exploratory';pm=json.loads((parent/'manifest.json').read_text())
samples=json.loads((parent/pm['dataset']/'samples.json').read_text())
summary={}
for cohort in ('direct','reconstruction'):
 selected=[r for r in samples if r['cohort']==cohort]
 summary[cohort]={}
 for scope in ('all','confirmed_non_st','confirmed_st','unknown'):
  filtered,meta=apply(selected,cohort,scope=scope,data=data)
  summary[cohort][scope]=dict(outcomes=dict(Counter(r['outcome'] for r in filtered)),**meta)
report=dict(version=VERSION,source_snapshot=pm['dataset'],status_counts=dict(Counter(r['historical_st_status'] for r in rows)),cohorts=summary,
            audit_693=data['summary.json']['audit_693'],policy='All facts retained; ST is a separate research attribute; trading eligibility separate')
(out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
(root/'manifest.json').write_text(json.dumps(dict(version=VERSION,dataset=stamp,files={n:hashlib.sha256((out/n).read_bytes()).hexdigest() for n in ('historical_st_status.parquet','summary.json')}),indent=2))
print(json.dumps({c:{s:v['outcomes'] for s,v in d.items()} for c,d in summary.items()},ensure_ascii=False))
