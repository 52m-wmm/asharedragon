"""Read-only price-basis audit. Writes evidence only; never fetches or changes prices.
Run: PYTHONPATH=backend .venv/bin/python scripts/audit_price_adjustment.py
BaoStock sample responses are separately cached in data/price_adjustment_audit.
"""
import hashlib
import json
from collections import Counter
from pathlib import Path
from zipfile import ZipFile
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from app.db.session import engine

ROOT=Path.home()/'Datasets/asharedragon/processed/stocks/daily/purchased_usb_0dfebf6ee9bc'
OUT=Path('data/price_adjustment_audit')
ARCHIVE=Path('/Volumes/NO NAME/股票1d线.zip')
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def run():
    OUT.mkdir(exist_ok=True,parents=True)
    files=sorted(ROOT.glob('year=*/month=*/*.parquet'))
    protected=[*files,ROOT/'manifest.json',ROOT/'audit.json']
    before={str(p):sha(p) for p in protected}
    db=duckdb.connect(config={'threads':2,'memory_limit':'1GB'})
    db.read_parquet([str(p) for p in files],hive_partitioning=False,filename=True).create_view('daily')
    def q(sql):
        r=db.execute(sql);return [dict(zip([v[0] for v in r.description],row)) for row in r.fetchall()]
    result={'protected_hashes_before':before,'schema':str(pq.read_schema(files[0])),
        'processed_schema_counts':dict(Counter(str(pq.read_schema(p)) for p in files)),
        'processed_metadata_keys':dict(Counter(str(sorted((pq.read_metadata(p).metadata or {}).keys())) for p in files))}
    result['partitions']=q('''select filename,count(*) rows,min(trade_date) first_date,max(trade_date) last_date,
        count(*) filter(where adjustment_type!='none') other_basis,
        count(*) filter(where abs(open*100-round(open*100))>1e-6 or abs(high*100-round(high*100))>1e-6 or abs(low*100-round(low*100))>1e-6 or abs(close*100-round(close*100))>1e-6) off_tick,
        count(*) filter(where volume>0 and (amount/volume<low-0.02 or amount/volume>high+0.02)) vwap_outside
        from daily group by 1 order by 1''')
    db.execute('''create temp table transitions as select *,lag(close) over w prev_close,lag(adj_factor) over w prev_factor,
        lag(trade_date) over w prev_date from daily window w as(partition by stock_code order by trade_date)''')
    result['yearly']=q('''select year(trade_date) AS year,count(*) rows,count(distinct stock_code) stocks,
        count(*) filter(where abs(adj_factor/prev_factor-1)>1e-8) factor_changes,
        count(*) filter(where abs(pre_close-prev_close)>0.011) reference_changes,
        count(*) filter(where abs(adj_factor/prev_factor-1)>1e-8 and abs(pre_close-prev_close)<=0.011) factor_only_changes,
        count(*) filter(where abs(adj_factor/prev_factor-1)>1e-8 and abs(pre_close*adj_factor/prev_factor-prev_close)>0.02) factor_bridge_errors
        from transitions group by 1 order by 1''')
    result['factor_only_dates']=q('''select trade_date,count(*) rows,max(abs(adj_factor/prev_factor-1)) max_relative_change
        from transitions where abs(adj_factor/prev_factor-1)>1e-8 and abs(pre_close-prev_close)<=0.011
        group by 1 order by rows desc limit 20''')
    with engine.connect() as c,c.begin():
        c.execute(text('SET TRANSACTION READ ONLY'))
        def pg(sql):return [dict(r) for r in c.execute(text(sql)).mappings()]
        result['pg_inventory']=pg('select provider,adjustment,count(*) rows,min(trade_date) first_date,max(trade_date) last_date from stock_daily group by 1,2')
        result['raw_inventory']=pg('select provider,endpoint,count(*) rows from raw_observations group by 1,2')
        refs=pg('''select distinct on(provider,stock_code,trade_date,adjustment) provider,stock_code,trade_date,adjustment,open,high,low,close from stock_daily order by provider,stock_code,trade_date,adjustment,id desc''')
        raw=pg("select id,raw_payload->'records' records from raw_observations where provider='tushare_proxy' and endpoint='limit_list_d' and ingestion_status='success' order by id")
    db.register('refs',pa.Table.from_pylist(refs))
    result['local_daily_comparison']=q('''select provider,adjustment,year(d.trade_date) AS year,count(*) rows,
        max(greatest(abs(d.open-r.open),abs(d.high-r.high),abs(d.low-r.low),abs(d.close-r.close))) max_ohlc_difference
        from daily d join refs r using(stock_code,trade_date) group by 1,2,3 order by 1,2,3''')
    events={}
    for batch in raw:
        for r in batch['records'] or []:
            if r.get('close') is not None:events[(r['ts_code'],r['trade_date'])]={'stock_code':r['ts_code'],'day':r['trade_date'],'close':float(r['close']),'raw_id':batch['id']}
    db.register('events',pa.Table.from_pylist(list(events.values())))
    result['tushare_events']=q("select year(d.trade_date) AS year,count(*) rows,max(abs(d.close-e.close)) max_close_difference from daily d join events e on d.stock_code=e.stock_code and replace(cast(d.trade_date as varchar),'-','')=replace(e.day,'-','') group by 1")
    # Check every imported original day, including schema/metadata; compare all retained numeric fields.
    schemas=Counter();metadata=Counter();source_days=[]
    with ZipFile(ARCHIVE) as z:
        result['zip_comment']=z.comment.decode(errors='replace')
        result['non_parquet_members']=[n for n in z.namelist() if not n.endswith('.parquet')]
        for n in z.namelist():
            if not n.endswith('.parquet') or not '20170101'<=Path(n).stem<='20260313':continue
            table=pq.read_table(pa.BufferReader(z.read(n)))
            schemas[str(table.schema.remove_metadata())]+=1
            metadata[str(sorted((table.schema.metadata or {}).keys()))]+=1
            db.register('original',table)
            day=Path(n).stem
            compared=q("""select count(*) rows,count(*) filter(where d.stock_code is null or
                o.open is distinct from d.open or o.high is distinct from d.high or o.low is distinct from d.low or o.close is distinct from d.close or
                o.pre_close is distinct from d.pre_close or o.adj_factor is distinct from d.adj_factor or o.vol is distinct from d.volume or o.amount is distinct from d.amount or o.vwap is distinct from d.vwap) differences
                from original o left join daily d on o.code=d.stock_code and strptime(o.date,'%Y%m%d')::date=d.trade_date""")[0]
            source_days.append(dict(member=n,**compared))
            if len(source_days)%300==0:print('source files checked',len(source_days),flush=True)
        result['source_metadata_example']={k.decode():v.decode(errors='replace') for k,v in (table.schema.metadata or {}).items()}
    result.update(source_schemas=dict(schemas),source_metadata_keys=dict(metadata),source_days=source_days)
    result['protected_hashes_unchanged']=all(sha(p)==before[str(p)] for p in protected)
    (OUT/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k in ['yearly','local_daily_comparison','tushare_events','factor_only_dates','protected_hashes_unchanged']},ensure_ascii=False,default=str,indent=2))
if __name__=='__main__':run()
