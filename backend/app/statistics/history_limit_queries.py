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
 for name in ('positives','cycles','daily'):db.read_parquet(str(path/(name+'.parquet'))).create_view(name)
 meta=json.loads((path/'summary.json').read_text())
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


def query(year=2026,month=None,trade_date=None,mode='all',height=1,view='daily',page=1,page_size=30,classification='all'):
 with dataset() as (db,meta):
  conditions=['year(trade_date)=?'];params=[year]
  if month:conditions.append('month(trade_date)=?');params.append(month)
  daily=rows(db,'SELECT * FROM daily WHERE '+' AND '.join(conditions)+' ORDER BY trade_date',params)
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
  return dict(daily=daily,samples=listing,total=total,page=page,page_size=page_size,selected_date=chosen,
      yearly=meta['years'],meta={k:v for k,v in meta.items() if k not in ('years','cases','benchmark_exceptions')},mode=mode,view=view,classification=classification)


def candles(code,day):
 with UnifiedResearch() as r:
  data=rows(r.db,"""SELECT stock_code,trade_date,open,high,low,close,volume,amount FROM stock_daily
    WHERE stock_code=? AND trade_date BETWEEN ? AND ? AND price_quality='verified' ORDER BY trade_date""",[code,day-timedelta(days=65),day+timedelta(days=35)])
 return dict(data=data,meta=dict(stock_code=code,event_date=day,adjustment='none',warning='既有未复权日线；缺失交易日不补零'))
