"""One dated stock-day membership for count, event, list and cohort queries.

This view never changes archived facts. IPO *special trading phase* is distinct
from an IPO-origin streak, which can continue after ordinary limits resume.
"""
from contextvars import ContextVar
from pathlib import Path
from functools import lru_cache
from datetime import date
import json
from app.research.unified import UnifiedResearch
from app.research.queries import DEFAULT_ROOT

VERSION='stock-scope-v1'
CHOICE=ContextVar('stock_scope',default=(False,False))
async def scope_context(exclude_st:bool=False,exclude_ipo:bool=False):
 token=CHOICE.set((exclude_st,exclude_ipo))
 try:yield
 finally:CHOICE.reset(token)


def ipo_phase(code,listing,day,calendar):
 if not listing or not day or day<listing:return 'unknown'
 board='star' if code.startswith('68') else 'chinext' if code.startswith('30') else 'main' if code.startswith(('00','60')) else 'unknown'
 if board=='unknown':return 'unknown'
 registered=board=='star' or board=='chinext' and listing>=date(2020,8,24) or board=='main' and listing>=date(2023,4,10)
 if listing not in calendar or day not in calendar:return 'unknown'
 age=calendar[day]-calendar[listing]+1
 return 'special' if 1<=age<=(5 if registered else 1) else 'regular' if age>0 else 'unknown'


def membership(db,meta,exclude_st=False,exclude_ipo=False):
 # H pins its U; never use a latest/current catalogue from a different parent.
 u=Path(meta['source_snapshot']);db.read_parquet(str(u/'stocks.parquet')).create_view('scope_stocks')
 db.execute("CREATE TEMP TABLE scope_calendar AS SELECT trade_date,row_number() OVER(ORDER BY trade_date) idx FROM read_parquet(?)",[str(u/'calendar.parquet')])
 db.execute("""CREATE TEMP VIEW scope_members AS WITH phase AS (
 SELECT d.stock_code,d.trade_date,d.st_status,CASE
 WHEN s.listing_date IS NULL OR d.trade_date<s.listing_date OR (s.delisting_date IS NOT NULL AND d.trade_date>s.delisting_date)
  OR d.board NOT IN ('main','star','chinext') OR a.idx IS NULL OR l.idx IS NULL THEN 'unknown'
 WHEN a.idx-l.idx+1<=CASE WHEN d.board='star' OR d.board='chinext' AND s.listing_date>=DATE '2020-08-24'
  OR d.board='main' AND s.listing_date>=DATE '2023-04-10' THEN 5 ELSE 1 END THEN 'special'
 ELSE 'regular' END ipo_phase
 FROM days d LEFT JOIN scope_stocks s USING(stock_code) LEFT JOIN scope_calendar a ON a.trade_date=d.trade_date
 LEFT JOIN scope_calendar l ON l.trade_date=s.listing_date)
 SELECT *,"""+("st_status='confirmed_non_st'" if exclude_st else 'TRUE')+" AND "+("ipo_phase='regular'" if exclude_ipo else 'TRUE')+" eligible FROM phase")
 return dict(version=VERSION,exclude_st=exclude_st,exclude_ipo=exclude_ipo,source_snapshot=str(u),
  unknown_policy='启用排除时：对应历史属性未知的记录不进入统计；全部保留未知。周期须每个已观察交易日满足条件。',
  ipo_basis='主板2023-04-10起/创业板2020-08-24起/科创板：上市首5个交易日；此前主板和创业板：特殊首日。按交易日历，不按自然天。IPO初始连板归属是独立属性。')


def apply_to_dataset(db,meta,exclude_st=False,exclude_ipo=False,mode='all',cycle_scope=False):
 info=membership(db,meta,exclude_st,exclude_ipo)
 # Counts of uncertain/excluded records are diagnostic background, never zero-filled qualification.
 info['yearly']=db.execute("""SELECT year(trade_date) AS year,count(*) observed,count(*) FILTER(WHERE eligible) included,
 count(*) FILTER(WHERE st_status='unknown') st_unknown,count(*) FILTER(WHERE ipo_phase='unknown') ipo_unknown,
 count(*) FILTER(WHERE ipo_phase='special') ipo_special FROM scope_members GROUP BY 1 ORDER BY 1""").fetchall()
 if not (exclude_st or exclude_ipo):meta['stock_scope']=info;return
 for t in ('days','positives','cycles','daily'):db.execute(f'CREATE TEMP VIEW original_{t} AS SELECT * FROM {t}')
 # Freeze sources before rebinding their view names (DuckDB resolves view refs dynamically).
 for t in ('days','positives','cycles','daily'):
  sql=db.execute("SELECT sql FROM duckdb_views() WHERE view_name=?",[t]).fetchall()[0][0]
  # The existing views are read_parquet; bind original names directly to the same files.
  db.execute(f'CREATE OR REPLACE TEMP VIEW original_{t} AS '+sql.split(' AS ',1)[1])
 db.execute('CREATE TEMP TABLE eligible_members AS SELECT * FROM scope_members')
 if cycle_scope:
  db.execute('''UPDATE eligible_members SET eligible=false WHERE (stock_code,trade_date) IN (
   SELECT p.stock_code,p.trade_date FROM original_positives p JOIN (
    SELECT q.stock_code,q.run_start FROM original_positives q JOIN eligible_members m USING(stock_code,trade_date)
    GROUP BY q.stock_code,q.run_start HAVING NOT bool_and(m.eligible)) bad USING(stock_code,run_start))''')
 db.execute('CREATE OR REPLACE TEMP VIEW days AS SELECT d.* FROM original_days d JOIN eligible_members m USING(stock_code,trade_date) WHERE m.eligible')
 db.execute('CREATE OR REPLACE TEMP VIEW positives AS SELECT p.* FROM original_positives p JOIN eligible_members m USING(stock_code,trade_date) WHERE m.eligible')
 db.execute('''CREATE OR REPLACE TEMP VIEW cycles AS SELECT c.* FROM original_cycles c WHERE NOT EXISTS
 (SELECT 1 FROM original_positives p JOIN eligible_members m USING(stock_code,trade_date)
 WHERE p.stock_code=c.stock_code AND p.run_start=c.run_start AND NOT m.eligible)''')
 db.execute('''CREATE OR REPLACE TEMP VIEW daily AS WITH agg AS (
 SELECT trade_date,count(*) observed_rows,count(*) FILTER(WHERE daily_present) daily_rows,
 count(*) FILTER(WHERE up AND status='confirmed') confirmed,count(*) FILTER(WHERE up AND status='reconstructed_candidate') reconstructed_candidate,
 count(*) FILTER(WHERE up IS NULL) unknown_observed,count(*) FILTER(WHERE up IS FALSE) not_up,count(*) FILTER(WHERE up IS NOT NULL) assessable
 FROM days GROUP BY 1), heights AS (SELECT trade_date,max(height) max_height,max(confirmed_height) confirmed_max_height FROM positives GROUP BY 1)
 SELECT a.trade_date,NULL::BIGINT expected_stock_days,coalesce(b.observed_rows,0) observed_rows,coalesce(b.daily_rows,0) daily_rows,
 CASE WHEN b.assessable>0 THEN b.confirmed END confirmed,CASE WHEN b.assessable>0 THEN b.reconstructed_candidate END reconstructed_candidate,
 coalesce(b.unknown_observed,0) unknown_observed,NULL::BIGINT missing_catalogue_rows,coalesce(b.unknown_observed,0) unknown,b.not_up,b.assessable,
 h.max_height,h.confirmed_max_height,NULL::DOUBLE data_coverage,NULL::DOUBLE classification_coverage
 FROM original_daily a LEFT JOIN agg b USING(trade_date) LEFT JOIN heights h USING(trade_date)''')
 meta['stock_scope']=info
 # Recompute summary instead of leaking unfiltered cards from the frozen summary.
 from app.statistics.history_limit_queries import rows
 years=[]
 for year in range(2017,2027):
  r=rows(db,f"SELECT coalesce(sum(confirmed),0) confirmed,coalesce(sum(reconstructed_candidate),0) reconstructed_candidate,coalesce(sum(unknown),0) unknown FROM daily WHERE year(trade_date)={year}")[0]
  r['year']=year;r['cycles']=rows(db,f"SELECT status,count(*) n,"+','.join(f'count(*) FILTER(WHERE height>={h}) ge{h}' for h in (2,3,5,7,10))+f' FROM cycles WHERE year(run_start)={year} GROUP BY status');years.append(r)
 meta['years']=years
 # Classification reports must describe exactly the retained cycles as well.
 if 'classification' in meta:
  meta['classification']={**meta['classification'],'yearly':rows(db,"SELECT year(c.run_start) AS year,k.ipo_origin,count(*) cycles,count(*) FILTER(WHERE final_height IS NOT NULL) exact_cycles,count(*) FILTER(WHERE final_height IS NULL) incomplete_cycles,count(*) FILTER(WHERE c.status='confirmed') confirmed_cycles,count(*) FILTER(WHERE c.st_status='unknown') st_unknown_cycles,count(*) FILTER(WHERE k.cycle_identity_quality='possible_fragment') possible_fragments,"+','.join(f'count(*) FILTER(WHERE c.height>={h}) ge{h}' for h in (3,5,7,10))+" FROM cycles c JOIN cycle_classes k USING(stock_code,run_start) GROUP BY 1,2 ORDER BY 1,2"),'turnover':rows(db,"SELECT year(c.run_start) AS year,count(*) cycles,count(*) FILTER(WHERE final_height IS NOT NULL) exact_cycles,count(*) FILTER(WHERE final_height IS NULL) incomplete_cycles,"+','.join(f'count(*) FILTER(WHERE c.height>={h}) ge{h}' for h in (3,5,7,10))+" FROM cycles c JOIN cycle_classes k USING(stock_code,run_start) WHERE k.turnover_candidate GROUP BY 1 ORDER BY 1")}


def progression(db,day,mode='all'):
 if day is None:return []
 from app.statistics.history_limit_queries import rows
 height='confirmed_height' if mode=='confirmed' else 'height'
 status=" AND p.status='confirmed'" if mode=='confirmed' else ''
 outcome="CASE WHEN d.status='confirmed' THEN d.up END" if mode=='confirmed' else 'd.up'
 return rows(db,f'''WITH prev AS (SELECT max(trade_date) AS day FROM scope_calendar WHERE trade_date<?), cohort AS (
 SELECT p.stock_code,p.{height} height,{outcome} up,d.status,d.session_index,m.eligible,q.{height} next_height
 FROM positives p JOIN prev ON p.trade_date=prev.day
 LEFT JOIN scope_members m ON m.stock_code=p.stock_code AND m.trade_date=?
 LEFT JOIN original_progress_days d ON d.stock_code=p.stock_code AND d.trade_date=?
 LEFT JOIN positives q ON q.stock_code=p.stock_code AND q.trade_date=?
 WHERE p.{height} IN (1,2){status})
 SELECT height,height+1 target,count(*) previous_cohort,
 count(*) FILTER(WHERE eligible) matched_cohort,
 count(*) FILTER(WHERE eligible AND up IS NOT NULL) denominator,
 count(*) FILTER(WHERE eligible AND next_height=height+1) numerator,
 count(*) FILTER(WHERE eligible IS NOT TRUE) excluded_current,
 count(*) FILTER(WHERE eligible AND up IS NULL) unknown,
 CASE WHEN count(*) FILTER(WHERE eligible AND up IS NULL)=0 THEN
 count(*) FILTER(WHERE eligible AND next_height=height+1)*1.0/nullif(count(*) FILTER(WHERE eligible),0) END rate
 FROM cohort GROUP BY height ORDER BY height''',[day,day,day,day])


@lru_cache(maxsize=4)
def catalogue(snapshot):
 import duckdb
 db=duckdb.connect();p=Path(snapshot)
 stocks=dict(db.execute('SELECT stock_code,listing_date FROM read_parquet(?)',[str(p/'stocks.parquet')]).fetchall())
 cal={d:i for i,(d,) in enumerate(db.execute('SELECT trade_date FROM read_parquet(?) ORDER BY trade_date',[str(p/'calendar.parquet')]).fetchall())};db.close();return stocks,cal


def filter_research(rows,source_snapshot=None):
 exclude_st,exclude_ipo=CHOICE.get()
 if not (exclude_st or exclude_ipo):return rows
 if not source_snapshot:
  base=Path(DEFAULT_ROOT)/'research/unified';source_snapshot=str(base/json.loads((base/'manifest.json').read_text())['dataset'])
 stocks,cal=catalogue(source_snapshot);out=[]
 for r in rows:
  state=r.get('historical_st',{}).get('status',r.get('st',r.get('st_status','unknown')))
  if exclude_st and state!='confirmed_non_st':continue
  dates=r.get('historical_st',{}).get('dates') or [r.get('first_board_date') or r.get('first_observed_date') or r.get('run_start')]
  phases=[ipo_phase(r['stock_code'],stocks.get(r['stock_code']),date.fromisoformat(str(d)) if d else None,cal) for d in dates]
  if exclude_ipo and (not phases or any(p!='regular' for p in phases)):continue
  out.append(r)
 return out
