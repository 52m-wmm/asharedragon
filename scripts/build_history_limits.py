"""One local SQL pass; derived Parquet only, no raw edits or remote requests."""
from pathlib import Path
import hashlib,json
from datetime import datetime,timezone
import duckdb
from app.research.unified import UnifiedResearch
from app.statistics.history_limits import VERSION,classification_sql,streak_sql
from app.statistics.reference_recovery import extract,SOURCE
ROOT=Path.home()/'Datasets/asharedragon/statistics/history_limits'

def save(path,x):path.write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str,allow_nan=False)+'\n')
def records(db,sql):
 c=db.execute(sql);return [dict(zip([x[0] for x in c.description],r)) for r in c.fetchall()]
def build():
 with UnifiedResearch() as u:
  source=u.path
  data_end=u.summary['data_end']
 key=hashlib.sha256((str(source)+Path('backend/app/statistics/history_limits.py').read_text()+Path(__file__).read_text()+Path('backend/app/statistics/reference_recovery.py').read_text()).encode()).hexdigest()[:20]
 out=ROOT/'snapshots'/key;out.mkdir(parents=True,exist_ok=True)
 db=duckdb.connect(config={'memory_limit':'2GB','threads':4});db.execute(f"SET temp_directory='{out}/tmp'")
 db.execute(f"ATTACH '{source}/research.duckdb' AS src (READ_ONLY)")
 db.execute("""CREATE TEMP TABLE cal AS SELECT trade_date,row_number() OVER(ORDER BY trade_date)-1 session_index FROM src.calendar""")
 recovery=extract(out/'recovered_references.parquet')
 db.read_parquet(str(out/'recovered_references.parquet')).create_view('recovered_refs')
 assert db.execute('SELECT count(*)-count(distinct(stock_code,trade_date)) FROM recovered_refs').fetchone()[0]==0
 db.execute("""CREATE TEMP VIEW input AS SELECT d.* EXCLUDE(pre_close),coalesce(d.pre_close,r.recovered_reference) pre_close,
     (d.pre_close IS NULL AND r.recovered_reference IS NOT NULL) reference_recovered,s.stock_name,s.listing_date,s.delisting_date,c.session_index,
     c.session_index-l.session_index+1 listing_session
     FROM src.stock_daily d LEFT JOIN recovered_refs r ON d.stock_code=r.stock_code AND d.trade_date=r.trade_date::DATE
       AND d.price_source='akshare_em_unadjusted_20260316_20261106' AND d.price_quality='verified'
       AND abs(d.close-r.raw_close)<1e-8 AND abs(d.open-r.raw_open)<1e-8 AND abs(d.high-r.raw_high)<1e-8 AND abs(d.low-r.raw_low)<1e-8
     LEFT JOIN src.stocks s ON s.stock_code=d.stock_code
     LEFT JOIN cal c ON c.trade_date=d.trade_date LEFT JOIN cal l ON l.trade_date=s.listing_date""")
 print('Classifying all existing daily rows',flush=True)
 db.execute('CREATE TEMP TABLE days AS '+classification_sql())
 target_rows=db.execute("SELECT count(*) FROM input WHERE price_quality='verified'").fetchone()[0]
 assert db.execute('SELECT count(*) FROM days WHERE daily_present').fetchone()[0]==target_rows
 assert db.execute('SELECT count(*)-count(distinct (stock_code,trade_date)) FROM days').fetchone()[0]==0
 recovery['joined_references']=db.execute('SELECT count(*) FROM input WHERE reference_recovered').fetchone()[0]
 recovery['outcomes']=records(db,"SELECT status,up,reason,count(*) n FROM days WHERE reference_basis='raw_close_minus_change_pct_checked' GROUP BY ALL ORDER BY n DESC")
 db.execute(f"COPY days TO '{out}/days.parquet' (FORMAT PARQUET,COMPRESSION ZSTD)")
 print('Computing calendar-contiguous streaks',flush=True)
 db.execute('CREATE TEMP TABLE positives AS '+streak_sql())
 db.execute(f"COPY positives TO '{out}/positives.parquet' (FORMAT PARQUET,COMPRESSION ZSTD)")
 db.execute("""CREATE TEMP TABLE cycles AS SELECT stock_code,run_start,min(first_board_date) first_board_date,
   max(trade_date) end_date,max(height) height,
   CASE WHEN count(*) FILTER(WHERE status='reconstructed_candidate')>0 THEN 'reconstructed_candidate' ELSE 'confirmed' END status,
   count(*) FILTER(WHERE status='confirmed') confirmed_days,count(*) FILTER(WHERE status='reconstructed_candidate') candidate_days,
   first(stock_name) stock_name,first(board) board,
   CASE WHEN count(*) FILTER(WHERE st_status='unknown')>0 THEN 'unknown' WHEN count(distinct st_status)>1 THEN 'mixed' ELSE first(st_status) END st_status,
   arg_max(exact_end,trade_date) end_complete,
   CASE WHEN arg_max(exact_end,trade_date) THEN max(trade_date) END cycle_end_date,
   CASE WHEN min(first_board_date) IS NOT NULL AND arg_max(exact_end,trade_date) THEN max(height) END final_height FROM positives GROUP BY stock_code,run_start""")
 db.execute(f"COPY cycles TO '{out}/cycles.parquet' (FORMAT PARQUET,COMPRESSION ZSTD)")
 db.execute(f"""CREATE TEMP TABLE daily AS WITH expected AS (
   SELECT c.trade_date,count(*) expected_stock_days,count(*) FILTER(WHERE d.stock_code IS NULL) missing_catalogue_rows FROM cal c JOIN src.stocks s ON c.trade_date>=s.listing_date AND (s.delisting_date IS NULL OR c.trade_date<=s.delisting_date)
   LEFT JOIN days d ON d.stock_code=s.stock_code AND d.trade_date=c.trade_date
   WHERE c.trade_date BETWEEN '2017-01-01' AND '{data_end}' GROUP BY c.trade_date
 ), observed AS (
   SELECT trade_date,count(*) observed_rows,count(*) FILTER(WHERE daily_present) daily_rows,
    count(*) FILTER(WHERE status='confirmed' AND up) confirmed,
    count(*) FILTER(WHERE status='reconstructed_candidate' AND up) reconstructed_candidate,
    count(*) FILTER(WHERE up IS NULL) unknown_observed,count(*) FILTER(WHERE up IS FALSE) not_up,
    count(*) FILTER(WHERE up IS NOT NULL) assessable FROM days GROUP BY trade_date
 ), heights AS (SELECT trade_date,max(height) max_height,max(confirmed_height) confirmed_max_height FROM positives GROUP BY trade_date)
 SELECT e.* EXCLUDE(missing_catalogue_rows),coalesce(o.observed_rows,0) observed_rows,coalesce(o.daily_rows,0) daily_rows,
   CASE WHEN o.assessable>0 THEN o.confirmed END confirmed,
   CASE WHEN o.assessable>0 THEN o.reconstructed_candidate END reconstructed_candidate,
   coalesce(o.unknown_observed,0) unknown_observed,
   e.missing_catalogue_rows missing_catalogue_rows,
   coalesce(o.unknown_observed,0)+e.missing_catalogue_rows unknown,
   o.not_up,o.assessable,h.max_height,h.confirmed_max_height,
   least(coalesce(o.daily_rows,0)*1.0/nullif(e.expected_stock_days,0),1) data_coverage,
   least(coalesce(o.assessable,0)*1.0/nullif(e.expected_stock_days,0),1) classification_coverage
 FROM expected e LEFT JOIN observed o USING(trade_date) LEFT JOIN heights h USING(trade_date) ORDER BY e.trade_date""")
 db.execute(f"COPY daily TO '{out}/daily.parquet' (FORMAT PARQUET,COMPRESSION ZSTD)")
 years=[]
 for year in range(2017,2027):
  counts=records(db,f"""SELECT count(*) FILTER(WHERE daily_present) daily_rows,count(*) FILTER(WHERE up AND status='confirmed') confirmed,
     count(*) FILTER(WHERE up AND status='reconstructed_candidate') reconstructed_candidate,
     count(*) FILTER(WHERE up IS NULL) unknown_observed FROM days WHERE year(trade_date)={year}""")[0]
  counts.update(year=year,**records(db,f"SELECT sum(missing_catalogue_rows)::BIGINT missing_catalogue_rows,sum(unknown)::BIGINT unknown FROM daily WHERE year(trade_date)={year}")[0])
  counts['cycles']=records(db,f"""SELECT status,count(*) runs,count(*) FILTER(WHERE height>=2) ge2,count(*) FILTER(WHERE height>=3) ge3,count(*) FILTER(WHERE height>=5) ge5,count(*) FILTER(WHERE height>=7) ge7,count(*) FILTER(WHERE height>=10) ge10 FROM cycles WHERE year(run_start)={year} GROUP BY status ORDER BY status""")
  counts['unknown_reasons']=records(db,f"SELECT reason,count(*) n FROM days WHERE year(trade_date)={year} AND up IS NULL GROUP BY reason ORDER BY n DESC")
  years.append(counts)
 # Real event benchmark is a priority/deduplication check, not independent validation of a fallback rule.
 bench=records(db,"""SELECT count(*) archive_events,count(*) FILTER(WHERE p.status='confirmed') confirmed_matches,
    count(*) FILTER(WHERE p.stock_code IS NULL) excluded_or_unknown
    FROM src.selected_events e LEFT JOIN positives p USING(stock_code,trade_date)
    WHERE e.provider='tushare_proxy' AND e.limit_type='up' AND e.trade_date BETWEEN '2020-12-01' AND '2020-12-31'""")[0]
 missing=records(db,"""SELECT e.stock_code,e.trade_date,d.reason FROM src.selected_events e LEFT JOIN days d USING(stock_code,trade_date)
    WHERE e.provider='tushare_proxy' AND e.limit_type='up' AND e.trade_date BETWEEN '2020-12-01' AND '2020-12-31' AND d.up IS NOT TRUE ORDER BY e.trade_date,e.stock_code""")
 cases=records(db,"""SELECT stock_code,trade_date,status,height,run_start,first_board_date,confirmed_height FROM positives WHERE
    (stock_code='603178.SH' AND trade_date BETWEEN '2023-09-28' AND '2023-10-25') OR
    (stock_code='603580.SH' AND trade_date BETWEEN '2024-03-05' AND '2024-03-21') ORDER BY stock_code,trade_date""")
 save(out/'summary.json',dict(version=VERSION,source_snapshot=str(source),daily_target_rows=target_rows,data_end=data_end,years=years,
     benchmark_2020_dec=bench,benchmark_exceptions=missing,cases=cases,
     coverage_basis='known lifecycle catalogue; observed stock-days and unknown missing grid rows separated',
     reference_policy='source pre_close or raw close-change with pct/OHLC consistency; observed breaks stay unknown; no lag fill',reference_recovery=recovery,
     cycle_policy='calendar-consecutive positive observations; unknown breaks runs; run_start is not necessarily exact first board',
     formal_research_eligible=False,built_at_utc=datetime.now(timezone.utc).isoformat()))
 save(ROOT/'manifest.json',dict(version=VERSION,dataset=str(out.relative_to(ROOT))))
 db.close();print(json.dumps({'snapshot':str(out),'benchmark':bench,'exceptions':missing,'case_days':len(cases)},default=str))
if __name__=='__main__':build()
