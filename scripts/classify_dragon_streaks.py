"""Classify existing cycles and their positive-day OHLC; no daily re-identification."""
from pathlib import Path
import json,hashlib
from scripts.finalize_research import sha
import duckdb,pyarrow as pa
from app.statistics.history_limit_queries import ROOT
from app.statistics.streak_classification import VERSION,classify_origin,morphology

def records(db,sql):
 c=db.execute(sql);return [dict(zip([x[0] for x in c.description],r)) for r in c.fetchall()]
def build(root=ROOT,source_dataset=None):
 ROOT=Path(root).resolve()
 m=json.loads((ROOT/'manifest.json').read_text())
 if source_dataset:m['dataset']=source_dataset
 source=(ROOT/m['dataset']).resolve()
 if not source.is_relative_to(ROOT/'snapshots'):raise ValueError('Invalid H snapshot')
 meta=json.loads((source/'summary.json').read_text());unified=Path(meta['source_snapshot'])
 key=hashlib.sha256((str(source)+Path(__file__).read_text()+Path('backend/app/statistics/streak_classification.py').read_text()).encode()).hexdigest()[:20]
 out=ROOT/'classifications'/key
 if out.exists():raise FileExistsError('Immutable classification already exists: '+str(out))
 out.mkdir(parents=True)
 db=duckdb.connect(config={'memory_limit':'1GB','threads':2})
 db.execute(f"ATTACH '{unified}/research.duckdb' AS src (READ_ONLY)")
 for name in ('cycles','positives','days'):db.read_parquet(str(source/(name+'.parquet'))).create_view(name)
 db.execute('CREATE TEMP TABLE cal AS SELECT trade_date,row_number() OVER(ORDER BY trade_date)-1 idx FROM src.calendar')
 # Scan existing derived decisions, not raw OHLC, for a separating non-limit day.
 db.execute("CREATE TEMP TABLE negative_days AS SELECT stock_code,trade_date,session_index FROM days WHERE up IS FALSE ORDER BY stock_code,trade_date")
 db.execute('CREATE TEMP TABLE cycle_context AS SELECT *,lag(end_date) OVER(PARTITION BY stock_code ORDER BY run_start) previous_segment_end FROM cycles')
 db.execute('''CREATE TEMP TABLE shapes AS SELECT p.stock_code,p.run_start,count(*) observed_days,
 count(*) FILTER(WHERE d.price_quality='verified' AND d.open>0 AND d.low>0 AND d.high>=greatest(d.open,d.close) AND d.low<=least(d.open,d.close)) valid_ohlc,
 count(*) FILTER(WHERE d.price_quality='verified' AND abs(d.high-d.low)<1e-8 AND abs(d.open-d.close)<1e-8) one_price_days,
 count(*) FILTER(WHERE d.price_quality='verified' AND d.high-d.low>1e-8) range_days,
 count(*) FILTER(WHERE d.volume>0) positive_volume_days
 FROM positives p LEFT JOIN src.stock_daily d USING(stock_code,trade_date) GROUP BY p.stock_code,p.run_start''')
 data=records(db,'''SELECT c.*,s.listing_date,l.idx listing_index,a.idx start_index,n.session_index first_negative,n.trade_date last_negative_date,
 sh.*,p.status first_day_status FROM cycle_context c LEFT JOIN src.stocks s USING(stock_code)
 LEFT JOIN cal l ON l.trade_date=s.listing_date LEFT JOIN cal a ON a.trade_date=c.run_start
 ASOF LEFT JOIN negative_days n ON c.stock_code=n.stock_code AND c.run_start>n.trade_date LEFT JOIN shapes sh ON sh.stock_code=c.stock_code AND sh.run_start=c.run_start
 LEFT JOIN positives p ON p.stock_code=c.stock_code AND p.trade_date=c.run_start''')
 classified=[]
 for r in data:
  listing=r['listing_date'];code=r['stock_code'];registered=bool(listing and (code.startswith('68') or code.startswith('30') and str(listing)>='2020-08-24' or code.startswith(('00','60')) and str(listing)>='2023-04-10'))
  separated=r['first_negative'] is not None and r['start_index'] is not None and r['first_negative']<r['start_index']
  origin,reason=classify_origin(r['listing_index'],r['start_index'],registered,separated,r['first_day_status']=='confirmed')
  shape,traded=morphology(r['observed_days'],r['valid_ohlc'],r['one_price_days'],r['range_days'],r['positive_volume_days'])
  classified.append(dict(stock_code=code,run_start=r['run_start'],ipo_origin=origin,classification_reason=reason,
    listing_date=listing,listing_session=None if r['listing_index'] is None or r['start_index'] is None else r['start_index']-r['listing_index']+1,
    ipo_regime='first_five_no_limit' if registered else 'legacy_special_first_day',ohlc_shape=shape,
    turnover_candidate=origin=='non_ipo' and traded,one_price_days=r['one_price_days'],range_days=r['range_days'],valid_ohlc_days=r['valid_ohlc'],
    classification_quality='unknown' if origin=='unknown' else 'catalogue_calendar_context',
    cycle_identity_quality='possible_fragment' if r['previous_segment_end'] is not None and (r['last_negative_date'] is None or r['last_negative_date']<=r['previous_segment_end']) else 'start_unknown' if r['first_board_date'] is None else 'separated_start',
    previous_segment_end=r['previous_segment_end'],last_negative_date=r['last_negative_date'],boundary_complete=r['final_height'] is not None))
 db.register('classified',pa.Table.from_pylist(classified))
 assert db.execute('SELECT count(*)-count(distinct(stock_code,run_start)) FROM cycles').fetchone()[0]==0
 assert db.execute('SELECT count(*)-count(distinct(stock_code,run_start)) FROM classified').fetchone()[0]==0
 assert db.execute('SELECT count(*) FROM positives').fetchone()[0]==db.execute('SELECT sum(height) FROM cycles').fetchone()[0]
 assert db.execute('SELECT count(*)-count(distinct(stock_code,trade_date)) FROM positives').fetchone()[0]==0
 # A positive day cannot belong to multiple retained segments, nor can segments overlap.
 assert db.execute('SELECT count(*) FROM (SELECT *,lag(end_date) OVER(PARTITION BY stock_code ORDER BY run_start) prev_end FROM cycles) WHERE prev_end>=run_start').fetchone()[0]==0
 db.execute(f"COPY classified TO '{out}/classifications.parquet' (FORMAT PARQUET,COMPRESSION ZSTD)")
 db.execute('CREATE TEMP VIEW enriched AS SELECT c.*,k.* EXCLUDE(stock_code,run_start) FROM cycles c JOIN classified k USING(stock_code,run_start)')
 yearly=records(db,'''SELECT year(run_start) AS year,ipo_origin,count(*) cycles,count(*) FILTER(WHERE final_height IS NOT NULL) exact_cycles,
 count(*) FILTER(WHERE final_height IS NULL) incomplete_cycles,count(*) FILTER(WHERE status='confirmed') confirmed_cycles,
 count(*) FILTER(WHERE st_status='unknown') st_unknown_cycles,count(*) FILTER(WHERE cycle_identity_quality='possible_fragment') possible_fragments,
 count(*) FILTER(WHERE height>=3) ge3,count(*) FILTER(WHERE height>=5) ge5,count(*) FILTER(WHERE height>=7) ge7,count(*) FILTER(WHERE height>=10) ge10
 FROM enriched GROUP BY ALL ORDER BY 1,2''')
 turnover=records(db,'''SELECT year(run_start) AS year,count(*) cycles,count(*) FILTER(WHERE final_height IS NOT NULL) exact_cycles,count(*) FILTER(WHERE final_height IS NULL) incomplete_cycles,
 count(*) FILTER(WHERE height>=3) ge3,count(*) FILTER(WHERE height>=5) ge5,count(*) FILTER(WHERE height>=7) ge7,count(*) FILTER(WHERE height>=10) ge10 FROM enriched WHERE turnover_candidate GROUP BY 1 ORDER BY 1''')
 for year in (2017,2018,2020,2023,2024,2026):
  db.execute(f"COPY (SELECT * FROM enriched WHERE year(run_start)={year} AND height>=10 ORDER BY run_start,stock_code) TO '{out}/audit_{year}_ge10.csv' (HEADER,DELIMITER ',')")
 summary=dict(version=VERSION,source_dataset=m['dataset'],source_snapshot=str(source),unified_snapshot=str(unified),cycle_count=len(classified),unique_cycle_count=len(classified),duplicate_keys=0,overlapping_cycles=0,positive_days=db.execute('SELECT count(*) FROM positives').fetchone()[0],yearly=yearly,turnover=turnover)
 (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
 inputs={'source_snapshot':str(source),'unified_snapshot':str(unified),'files':{str(p):sha(p) for p in [source/'cycles.parquet',source/'positives.parquet',source/'days.parquet',source/'summary.json',unified/'research.duckdb',unified/'stocks.parquet',unified/'calendar.parquet']}}
 (out/'inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
 manifest=dict(version=VERSION,dataset=str(out.relative_to(ROOT)),source_dataset=m['dataset'],files={p.name:sha(p) for p in out.iterdir() if p.is_file()})
 (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 print(json.dumps(dict(path=str(out),audit2017=records(db,"SELECT ipo_origin,count(*) n FROM enriched WHERE year(run_start)=2017 AND height>=10 GROUP BY 1")),ensure_ascii=False))
 db.close()
 return out

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--source-dataset');a=p.parse_args();build(source_dataset=a.source_dataset)
