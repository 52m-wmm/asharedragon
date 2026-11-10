from pathlib import Path
from contextlib import contextmanager
from datetime import timedelta
import json
import duckdb
from app.research.unified import UnifiedResearch

ROOT=Path.home()/'Datasets/asharedragon/statistics/history_limits'

def rows(db,sql,args=None):
 c=db.execute(sql,args or []);return [dict(zip([x[0] for x in c.description],r)) for r in c.fetchall()]

@contextmanager
def dataset():
 manifest=json.loads((ROOT/'manifest.json').read_text());path=(ROOT/manifest['dataset']).resolve()
 if not path.is_relative_to(ROOT):raise ValueError('Invalid statistics snapshot')
 db=duckdb.connect(config={'threads':2,'memory_limit':'512MB'})
 for name in ('positives','cycles','daily','days'):db.read_parquet(str(path/(name+'.parquet'))).create_view(name)
 meta=json.loads((path/'summary.json').read_text())
 meta['availability']={
  'daily_through':db.execute('SELECT max(trade_date) FROM daily WHERE daily_rows>0').fetchall()[0][0],
  'confirmed_through':db.execute("SELECT max(trade_date) FROM positives WHERE status='confirmed'").fetchall()[0][0],
 }
 classification_manifest=ROOT/'classification_manifest.json'
 if classification_manifest.exists():
  cm=json.loads(classification_manifest.read_text())
  cp=(ROOT/cm['dataset']).resolve()
  if cm['source_dataset']==manifest['dataset'] and cp.is_relative_to(ROOT):
   db.read_parquet(str(cp/'classifications.parquet')).create_view('cycle_classes')
   meta['classification']=json.loads((cp/'summary.json').read_text())
 if 'classification' not in meta:
  db.execute("CREATE VIEW cycle_classes AS SELECT stock_code,run_start,'unknown' ipo_origin,false turnover_candidate,'unknown' ohlc_shape,'unknown' cycle_identity_quality,NULL::DATE listing_date FROM cycles")
 try:yield db,meta
 finally:db.close()



def intraday_series(db,start,end,mode):
 # Aggregate the whole selected period once, using the same snapshot as the cards.
 status=" AND status='confirmed'" if mode=='confirmed' else ''
 return rows(db,"""WITH events AS (
   SELECT DISTINCT stock_code,trade_date,limit_type FROM source_events WHERE trade_date BETWEEN ? AND ?
 ), touched AS (
   SELECT stock_code,trade_date FROM days WHERE trade_date BETWEEN ? AND ?
     AND (up IS TRUE OR daily_shape='touch_unsealed')"""+status+"""
   UNION SELECT stock_code,trade_date FROM events WHERE limit_type IN ('up','broken')
 ), totals AS (
   SELECT trade_date,count(*) touched FROM touched GROUP BY trade_date
 ), negatives AS (
   SELECT trade_date,nullif(count(DISTINCT stock_code) FILTER(WHERE limit_type='broken'),0) broken,
    nullif(count(DISTINCT stock_code) FILTER(WHERE limit_type='down'),0) down
   FROM events GROUP BY trade_date
 ) SELECT coalesce(t.trade_date,n.trade_date) trade_date,t.touched,n.broken,n.down
 FROM totals t FULL JOIN negatives n USING(trade_date) ORDER BY trade_date""",[start,end,start,end])


def intraday_counts(db,day,mode):
 result=intraday_series(db,day,day,mode)
 return {k:result[0][k] if result else None for k in ('touched','broken','down')}

def query(year=2026,month=None,trade_date=None,mode='all',height=1,view='daily',page=1,page_size=30,classification='all'):
 with dataset() as (db,meta):
  conditions=['year(trade_date)=?'];params=[year]
  if month:conditions.append('month(trade_date)=?');params.append(month)
  daily=rows(db,'SELECT * FROM daily WHERE '+' AND '.join(conditions)+' ORDER BY trade_date',params)
  db.read_parquet(str(Path(meta['source_snapshot'])/'events.parquet')).create_view('source_events')
  signals={r['trade_date']:r for r in intraday_series(db,daily[0]['trade_date'],daily[-1]['trade_date'],mode)} if daily else {}
  for item in daily:
   item.update({k:signals.get(item['trade_date'],{}).get(k) for k in ('touched','broken','down')})
  if trade_date and (trade_date.year!=year or month and trade_date.month!=month):raise ValueError('日期不在当前年月筛选范围')
  if view=='daily':
   chosen=trade_date or (daily[-1]['trade_date'] if daily else None)
   wh=['trade_date=?',('confirmed_height' if mode=='confirmed' else 'height')+'>=?'];args=[chosen,height]
   if mode=='confirmed':wh.append("status='confirmed'")
   sql='SELECT * FROM positives WHERE '+' AND '.join(wh)
   sort=('confirmed_height' if mode=='confirmed' else 'height')+' DESC,stock_code'
  else:
   chosen=None;wh=['year(run_start)=?','height>=?'];args=[year,height]
   if month:wh.append('month(run_start)=?');args.append(month)
   # Only-confirmed cycles use contiguous confirmed-only segments, never candidate height.
   source='(SELECT c.*,k.* EXCLUDE(stock_code,run_start) FROM cycles c LEFT JOIN cycle_classes k USING(stock_code,run_start))'
   if mode=='confirmed':
    source="""(SELECT stock_code,min(run_start) parent_run_start,confirmed_run_start run_start,CASE WHEN min(first_board_date)=confirmed_run_start THEN min(first_board_date) END first_board_date,max(trade_date) end_date,
       max(confirmed_height) height,'confirmed' status,first(stock_name) stock_name,first(board) board,
       count(*) confirmed_days,0 candidate_days,
       CASE WHEN count(*) FILTER(WHERE st_status='unknown')>0 THEN 'unknown' WHEN count(distinct st_status)>1 THEN 'mixed' ELSE first(st_status) END st_status,
       CASE WHEN arg_max(exact_end,trade_date) THEN max(trade_date) END cycle_end_date,
       CASE WHEN min(first_board_date)=confirmed_run_start AND arg_max(exact_end,trade_date) THEN max(confirmed_height) END final_height FROM positives WHERE status='confirmed' GROUP BY stock_code,confirmed_run_start)"""
   if mode=='confirmed':source='(SELECT c.*,k.* EXCLUDE(stock_code,run_start) FROM '+source+' c LEFT JOIN cycle_classes k ON c.stock_code=k.stock_code AND c.parent_run_start=k.run_start)'
   if classification=='exclude_ipo':wh.append("coalesce(ipo_origin,'unknown')<>'ipo_initial'")
   if classification=='turnover':wh.append('turnover_candidate IS TRUE')
   sql='SELECT * FROM '+source+' WHERE '+' AND '.join(wh);sort='height DESC,run_start DESC,stock_code'
  total=db.execute('SELECT count(*) FROM ('+sql+')',args).fetchone()[0]
  listing=rows(db,sql+' ORDER BY '+sort+' LIMIT ? OFFSET ?',args+[page_size,(page-1)*page_size])
  intraday=None
  if view=='daily' and chosen:
   intraday={k:signals.get(chosen,{}).get(k) for k in ('touched','broken','down')}
  touch_samples=[];touch_total=0
  if view=='daily' and 'daily_shape' in [x[0] for x in db.execute('DESCRIBE days').fetchall()]:
   touch_where="trade_date=? AND daily_shape='touch_unsealed'"+(" AND status='confirmed'" if mode=='confirmed' else '')
   touch_total=db.execute('SELECT count(*) FROM days WHERE '+touch_where,[chosen]).fetchone()[0]
   touch_samples=rows(db,'SELECT stock_code,stock_name,trade_date,st_status,status,high,close FROM days WHERE '+touch_where+' ORDER BY stock_code LIMIT 100',[chosen])
  return dict(intraday=intraday,touch_samples=touch_samples,touch_total=touch_total,daily=daily,samples=listing,total=total,page=page,page_size=page_size,selected_date=chosen,
      yearly=meta['years'],meta={k:v for k,v in meta.items() if k not in ('years','cases','benchmark_exceptions')},mode=mode,view=view,classification=classification)


def candles(code,day):
 with UnifiedResearch() as r:
  data=rows(r.db,"""SELECT stock_code,trade_date,open,high,low,close,volume,amount FROM stock_daily
    WHERE stock_code=? AND trade_date BETWEEN ? AND ? AND price_quality='verified' ORDER BY trade_date""",[code,day-timedelta(days=65),day+timedelta(days=35)])
 return dict(data=data,meta=dict(stock_code=code,event_date=day,adjustment='none',warning='既有未复权日线；缺失交易日不补零'))
