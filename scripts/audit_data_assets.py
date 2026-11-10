"""Read-only audit of original archive, imported bars, and locally held references."""
from collections import defaultdict
from datetime import date,datetime,timezone
from pathlib import Path
import json,io,zipfile
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from app.db.session import engine
from app.research.queries import DEFAULT_ROOT
from import_historical_daily import digest,member_date,normalize


def audit():
 root=DEFAULT_ROOT;out=Path('data/asset_audit')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');out.mkdir(parents=True)
 report={'checked_at':datetime.now(timezone.utc).isoformat(),'scope':'read-only; no downloads or edits to source data','sources':[]}
 db=duckdb.connect(config={'memory_limit':'1GB','threads':2})
 def rows(sql,args=None):
  c=db.execute(sql,args or []);names=[x[0] for x in c.description];return [dict(zip(names,r)) for r in c.fetchall()]
 def save(): (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,default=str,indent=2))
 staging=root/'staging/stocks/daily/purchased_usb_0dfebf6ee9bc';m=json.loads((staging/'manifest.json').read_text());archive=Path(m['archive'])
 print('Checking archive hash and original member contents',flush=True)
 original={'archive':str(archive),'sha256_matches':digest(archive)==m['sha256'],'months':[],'out_of_import_scope_rows':0,'total_rows':0,'members':0,'bad_members':[]}
 report['archive']=original
 import sys
 if len(sys.argv)>1:
  previous=json.loads(Path(sys.argv[1]).read_text())
  if not original['sha256_matches']:raise ValueError('Archive changed')
  report['archive']=previous['archive']
 else:
  groups=defaultdict(list)
  with zipfile.ZipFile(archive) as z:
   for info in z.infolist():
    d=member_date(info)
    if d:groups[str(d)[:7]].append((d,info))
   original['first_date']=min(d for g in groups.values() for d,_ in g);original['last_date']=max(d for g in groups.values() for d,_ in g)
   for month,entries in sorted(groups.items()):
    tables=[];count=0
    for d,info in entries:
     try:
      # ZipFile.read verifies CRC. Arrow reads real rows, not merely directory counts.
      t=pq.read_table(pa.BufferReader(z.read(info)))
      count+=t.num_rows;original['members']+=1
      if date.fromisoformat(m['start'])<=d<=date.fromisoformat(m['end']):tables.append(normalize(t,d,m['source'],db))
     except Exception as exc:
      original['bad_members'].append({'file':info.filename,'error':str(exc)[:250]})
    original['total_rows']+=count
    if not tables:original['out_of_import_scope_rows']+=count;continue
    db.register('original_month',pa.concat_tables(tables));entry=m['months'].get(month)
    result={'month':month,'original_rows':sum(t.num_rows for t in tables)}
    if entry and Path(entry['path']).exists():
     db.read_parquet(entry['path'],hive_partitioning=False).create_view('stage',replace=True)
     result['original_only']=rows('SELECT count(*) n FROM (SELECT * FROM original_month EXCEPT ALL SELECT * FROM stage)')[0]['n']
     result['staged_only']=rows('SELECT count(*) n FROM (SELECT * FROM stage EXCEPT ALL SELECT * FROM original_month)')[0]['n']
     result['staging_hash_matches']=digest(Path(entry['path']))==entry['sha256']
    else:result['missing_staging']=True
    original['months'].append(result)
    if len(original['months'])%24==0:print('Original import compared through',month,flush=True)
 save()
 with engine.connect() as conn,conn.begin():
  conn.execute(text('SET TRANSACTION READ ONLY'))
  ref=[dict(r) for r in conn.execute(text("SELECT DISTINCT ON(provider,stock_code,trade_date,adjustment) provider,stock_code,trade_date,adjustment,open,high,low,close,volume,amount FROM stock_daily ORDER BY provider,stock_code,trade_date,adjustment,id DESC")).mappings()]
  cal=[dict(r) for r in conn.execute(text("SELECT DISTINCT trade_date FROM trading_days WHERE exchange='SSE'")).mappings()]
 db.register('reference',pa.Table.from_pylist(ref));db.register('calendar',pa.Table.from_pylist(cal))
 db.read_parquet(str(root/'research/stocks.parquet')).create_view('stocks')
 for mp in sorted((root/'processed/stocks/daily').glob('*/manifest.json')):
  manifest=json.loads(mp.read_text());source=manifest['source'];print('Checking',source,flush=True)
  paths=[];hash_errors=[];release_diff=0
  for month,entry in manifest['months'].items():
   p=mp.parent/entry['file'];paths.append(str(p))
   if not p.exists() or digest(p)!=entry['sha256']:hash_errors.append(month)
   if source==m['source']:
    db.read_parquet(str(p),hive_partitioning=False).create_view('released',replace=True)
    db.read_parquet(m['months'][month]['path'],hive_partitioning=False).create_view('stage',replace=True)
    release_diff+=rows("SELECT count(*) n FROM ((SELECT * EXCLUDE(adjustment_type) FROM released EXCEPT ALL SELECT * EXCLUDE(adjustment_type) FROM stage) UNION ALL (SELECT * EXCLUDE(adjustment_type) FROM stage EXCEPT ALL SELECT * EXCLUDE(adjustment_type) FROM released))")[0]['n']
  rel=db.read_parquet(paths,union_by_name=True,hive_partitioning=False);rel.create_view('bars',replace=True)
  r={'source':source,'hash_mismatch_months':hash_errors,'staging_to_processed_differences':release_diff if source==m['source'] else None}
  r['quality']=rows('''SELECT count(*) n,count(DISTINCT stock_code) stocks,min(trade_date) first_date,max(trade_date) last_date,
   count(*)-count(DISTINCT (stock_code,trade_date)) duplicates,
   count(*) FILTER(WHERE open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR volume IS NULL OR amount IS NULL) missing_ohlcv,
   count(*) FILTER(WHERE NOT isfinite(open) OR NOT isfinite(high) OR NOT isfinite(low) OR NOT isfinite(close) OR open<=0 OR low<=0 OR high<greatest(open,close,low) OR low>least(open,close) OR volume<0 OR amount<0 OR NOT isfinite(volume) OR NOT isfinite(amount)) invalid_ohlcv,
   count(*) FILTER(WHERE abs(close*100-round(close*100))>0.00001) off_tick_close,
   count(*) FILTER(WHERE pre_close IS NULL) missing_reference,
   count(*) FILTER(WHERE volume>0 AND (amount/volume<low-.02 OR amount/volume>high+.02)) implied_vwap_outside_bar
   FROM bars''')[0]
  r['unexpected_dates']=rows('SELECT DISTINCT trade_date FROM bars ANTI JOIN calendar USING(trade_date) ORDER BY trade_date')
  r['reference_comparison']=rows('''SELECT r.provider,r.adjustment,count(*) AS overlap_rows,
   count(*) FILTER(WHERE abs(b.open-r.open)>.000001 OR abs(b.high-r.high)>.000001 OR abs(b.low-r.low)>.000001 OR abs(b.close-r.close)>.000001) price_differences,
   count(*) FILTER(WHERE abs(b.volume-r.volume)>1) volume_differences,
   count(*) FILTER(WHERE abs(b.amount-r.amount)>greatest(1,abs(r.amount)*.000001)) amount_differences
   FROM bars b JOIN reference r USING(stock_code,trade_date) WHERE r.adjustment='none' GROUP BY 1,2''')
  r['price_difference_examples']=rows('''SELECT b.stock_code,b.trade_date,r.provider,b.close local_close,r.close reference_close,b.open local_open,r.open reference_open FROM bars b JOIN reference r USING(stock_code,trade_date) WHERE r.adjustment='none' AND (abs(b.open-r.open)>.000001 OR abs(b.high-r.high)>.000001 OR abs(b.low-r.low)>.000001 OR abs(b.close-r.close)>.000001) ORDER BY b.trade_date,b.stock_code LIMIT 30''')
  r['lifecycle_gaps']=rows('''WITH span AS(SELECT min(trade_date) lo,max(trade_date) hi FROM bars), expected AS(
    SELECT s.stock_code,c.trade_date FROM stocks s JOIN calendar c ON c.trade_date>=s.listing_date AND (s.delisting_date IS NULL OR c.trade_date<=s.delisting_date),span
    WHERE c.trade_date BETWEEN lo AND hi AND (s.stock_code LIKE '%.SH' OR s.stock_code LIKE '%.SZ'))
    SELECT year(e.trade_date) AS year,count(*) expected_stock_days,count(*) FILTER(WHERE b.stock_code IS NULL) missing_stock_days
    FROM expected e LEFT JOIN bars b USING(stock_code,trade_date) GROUP BY 1 ORDER BY 1''')
  r['gap_note']='Missing rows against existing local lifecycle catalogue; suspensions and catalogue errors unresolved, not proven lost data.'
  report['sources'].append(r);save()
 db.close();print('Report:',out/'report.json',flush=True)
if __name__=='__main__':audit()
