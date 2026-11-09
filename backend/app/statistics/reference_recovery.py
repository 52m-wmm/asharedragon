"""Recover source-implied references from existing local raw checkpoints only."""
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json,hashlib
import pyarrow as pa
import pyarrow.parquet as pq

SOURCE='akshare_em_unadjusted_20260316_20261106'
RAW=Path.home()/'Datasets/asharedragon/staging/stocks/daily'/SOURCE/'symbols'

def recover_reference(raw):
 try:
  close,change,pct=(Decimal(str(raw[k])) for k in ('收盘','涨跌额','涨跌幅'))
  if not all(x.is_finite() for x in (close,change,pct)):return None
  if any(x!=x.quantize(Decimal('.01')) for x in (close,change)):return None
  reference=close-change
  if close<=0 or reference<=0:return None
  if abs(change/reference*100-pct)>Decimal('.0051'):return None
  return float(reference)
 except (KeyError,InvalidOperation,ValueError,TypeError):return None

def extract(destination):
 records=[];digest=hashlib.sha256();raw_count=0;rejected=0
 for path in sorted(RAW.glob('*.json')):
  content=path.read_bytes();digest.update(path.name.encode());digest.update(content)
  saved=json.loads(content)
  for raw in saved['raw']['records']:
   day=str(raw['日期'])[:10]
   if not '2026-03-16'<=day<='2026-11-06':continue
   raw_count+=1;ref=recover_reference(raw)
   if ref is None:rejected+=1;continue
   if str(raw.get('股票代码','')).zfill(6)!=saved['code'][:6]:raise ValueError('raw code mismatch')
   records.append(dict(stock_code=saved['code'],trade_date=day,recovered_reference=ref,
      raw_close=float(raw['收盘']),raw_open=float(raw['开盘']),raw_high=float(raw['最高']),raw_low=float(raw['最低'])))
 table=pa.Table.from_pylist(records);pq.write_table(table,destination,compression='zstd')
 return dict(source=SOURCE,raw_records=raw_count,recoverable=len(records),rejected=rejected,raw_sha256=digest.hexdigest(),
     method='raw close minus raw change; percentage rounding consistency; OHLC match; adjacent reference continuity checked separately; never lag-fill')
