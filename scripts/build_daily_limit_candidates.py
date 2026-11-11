"""Build an isolated, immutable 2024-2026 observation/candidate inventory, never active."""
import json,hashlib,calendar
from pathlib import Path
from datetime import date
import pyarrow as pa
from app.statistics.daily_limit_candidates import VERSION,candidate_sql
from app.analytics.rules import regular_rates
from scripts.build_market_breadth import open_inputs,calculate
from app.statistics.market_sentiment import active_signature,releases
from app.statistics.history_limit_queries import rows

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str,allow_nan=False)+'\n')

def build():
 signature=active_signature();paths,versions,days=releases(signature)
 end=days[-1];start='2024-01-01'
 bi=json.loads((paths['E1']/'inputs.json').read_text());lifecycle=Path(bi['lifecycle_report'])
 inputs={'version':VERSION,'start':start,'end':end,'parents':versions,'lifecycle_path':str(lifecycle),'lifecycle_sha256':sha(lifecycle),
 'code_sha256':{p:sha(p) for p in ['scripts/build_daily_limit_candidates.py','backend/app/statistics/daily_limit_candidates.py','backend/app/analytics/rules.py','backend/app/statistics/market_breadth.py','scripts/build_market_breadth.py']},
 'policy':{'scope':'SSE/SZSE observed A shares; outside scope retained unknown','full_market_coverage':None,'broken':'daily high equals candidate upper limit and close below; no proof of prior seal','confirmed':'compatible observed supplier positive event; not official certification','negative_events':'absence from pool is never confirmed negative','st_unknown':'preserve both 5/10 scenarios before 2026-07-06; positive candidate is any compatible scenario; lower count requires every scenario','special_sessions':'IPO first five, delisting window, gaps/resumption quarantined; other undocumented exceptions remain a candidate assumption','validation':'AKShare target period; Tushare 2020-01/06/12 method check only; event labels not input to candidate rule'}}
 key=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()[:20]
 out=paths['U'].parents[3]/'statistics/historical_daily_limits_candidate/snapshots'/key
 if out.exists():
  manifest=json.loads((out/'manifest.json').read_text())
  assert all(sha(out/f)==h for f,h in manifest['files'].items())
  print('Verified existing '+str(out));return out
 out.mkdir(parents=True)
 save(out/'inputs.json',inputs)
 db=open_inputs(paths['U'],paths['H']/'recovered_references.parquet',json.loads(lifecycle.read_text()))
 try:
  db.execute('CREATE TEMP TABLE listing_cal AS SELECT *,row_number() OVER(ORDER BY trade_date) idx FROM cal')
  all_days=[r[0] for r in db.execute('SELECT trade_date FROM cal').fetchall()]
  rules=[]
  for d in all_days:
   if not ('2020-01-01'<=str(d)<=end):continue
   for board in ('main','chinext','star'):
    for identity,st in [('confirmed_non_st',False),('confirmed_st',True),('unknown',None)]:
     for rate in regular_rates(board,d,st):rules.append({'trade_date':d,'board':board,'identity_status':identity,'rate':rate})
  db.register('rate_options',pa.Table.from_pylist(rules))
  db.execute("CREATE TEMP VIEW events_all AS SELECT * FROM src.events WHERE provider IN ('akshare','tushare_proxy')")
  save(out/'event_coverage.json',rows(db,"SELECT provider,limit_type,year(trade_date) AS year,min(trade_date) first_date,max(trade_date) last_date,count(DISTINCT trade_date) AS days,count(*) records FROM events_all GROUP BY ALL ORDER BY ALL"))
  db.execute("CREATE TEMP TABLE event_flags AS SELECT stock_code,trade_date,bool_or(limit_type='up') event_up,bool_or(limit_type='down') event_down,bool_or(limit_type='broken') event_broken,list(DISTINCT provider) event_providers,list(DISTINCT raw_id) event_raw_ids,(bool_or(limit_type='up') AND (bool_or(limit_type='down') OR bool_or(limit_type='broken'))) event_type_conflict FROM events_all GROUP BY 1,2")
  months=sorted({d[:7] for d in days if d>=start}|{'2020-01','2020-06','2020-12'})
  files=[]
  for month in months:
   y,m=map(int,month.split('-'));last=min(f'{month}-{calendar.monthrange(y,m)[1]}',end)
   calculate(db,month+'-01',last)
   db.execute('CREATE OR REPLACE TEMP TABLE candidate AS '+candidate_sql())
   db.execute("""CREATE OR REPLACE TEMP TABLE facts AS SELECT c.*,e.event_providers,e.event_raw_ids,e.event_up,e.event_down,e.event_broken,
    coalesce(e.event_type_conflict,false) event_type_conflict,
    CASE WHEN e.event_type_conflict OR c.event_conflict OR c.trade_status IS FALSE OR c.price_reason='price_conflict' OR c.candidate_reason IN ('ipo_no_limit_first_five','lifecycle_unknown_or_outside','outside_scope','outside_rule_scope') THEN 'unknown'
     WHEN e.event_up AND c.up_candidate IS FALSE THEN 'unknown' WHEN e.event_up THEN 'confirmed' WHEN c.up_candidate IS NOT NULL THEN 'candidate' ELSE 'unknown' END up_status,
    CASE WHEN e.event_type_conflict OR c.event_conflict OR c.trade_status IS FALSE OR c.price_reason='price_conflict' OR c.candidate_reason IN ('ipo_no_limit_first_five','lifecycle_unknown_or_outside','outside_scope','outside_rule_scope') THEN NULL
     WHEN e.event_up AND c.up_candidate IS FALSE THEN NULL WHEN e.event_up THEN TRUE ELSE c.up_candidate END up,
    CASE WHEN e.event_type_conflict OR c.event_conflict OR c.trade_status IS FALSE OR c.price_reason='price_conflict' OR c.candidate_reason IN ('ipo_no_limit_first_five','lifecycle_unknown_or_outside','outside_scope','outside_rule_scope') THEN 'unknown'
     WHEN e.event_down AND c.down_candidate IS FALSE THEN 'unknown' WHEN e.event_down THEN 'confirmed' WHEN c.down_candidate IS NOT NULL THEN 'candidate' ELSE 'unknown' END down_status,
    CASE WHEN e.event_type_conflict OR c.event_conflict OR c.trade_status IS FALSE OR c.price_reason='price_conflict' OR c.candidate_reason IN ('ipo_no_limit_first_five','lifecycle_unknown_or_outside','outside_scope','outside_rule_scope') THEN NULL
     WHEN e.event_down AND c.down_candidate IS FALSE THEN NULL WHEN e.event_down THEN TRUE ELSE c.down_candidate END down,
    CASE WHEN e.event_type_conflict OR c.event_conflict OR c.trade_status IS FALSE OR c.price_reason='price_conflict' OR c.candidate_reason IN ('ipo_no_limit_first_five','lifecycle_unknown_or_outside','outside_scope','outside_rule_scope') THEN 'unknown'
     WHEN e.event_broken AND c.broken_candidate IS FALSE THEN 'unknown' WHEN e.event_broken THEN 'confirmed' WHEN c.broken_candidate IS NOT NULL THEN 'candidate' ELSE 'unknown' END broken_status,
    CASE WHEN e.event_type_conflict OR c.event_conflict OR c.trade_status IS FALSE OR c.price_reason='price_conflict' OR c.candidate_reason IN ('ipo_no_limit_first_five','lifecycle_unknown_or_outside','outside_scope','outside_rule_scope') THEN NULL
     WHEN e.event_broken AND c.broken_candidate IS FALSE THEN NULL WHEN e.event_broken THEN TRUE ELSE c.broken_candidate END broken,
    coalesce((e.event_up AND c.up_candidate IS FALSE) OR (e.event_down AND c.down_candidate IS FALSE) OR (e.event_broken AND c.broken_candidate IS FALSE),false) event_rule_conflict
    FROM candidate c LEFT JOIN event_flags e USING(stock_code,trade_date)""")
   assert db.execute('SELECT count(*)-count(DISTINCT(stock_code,trade_date)) FROM facts').fetchall()[0][0]==0
   filename=month+'.parquet';db.execute('COPY facts TO ? (FORMAT PARQUET,COMPRESSION ZSTD)',[str(out/filename)]);files.append(filename);print(month,db.execute('SELECT count(*) FROM facts').fetchall()[0][0],flush=True)
  db.read_parquet([str(out/f) for f in files]).create_view('all_facts')
  db.execute("CREATE TEMP VIEW target AS SELECT * FROM all_facts WHERE trade_date>=DATE '2024-01-01'")
  metrics=[]
  for k in ('up','down','broken'):
   metrics.extend([f"count(*) FILTER(WHERE {k} AND {k}_status='confirmed') {k}_confirmed",f"count(*) FILTER(WHERE {k} AND {k}_status='candidate') {k}_candidate",f"count(*) FILTER(WHERE {k} IS NULL) {k}_unknown",f"count(*) FILTER(WHERE {k} IS NOT NULL) {k}_assessable",f"count(*) FILTER(WHERE {k}_status='candidate' AND {k} AND {k}_all_scenarios) {k}_scenario_lower",f"CASE WHEN count(*) FILTER(WHERE {k} IS NOT NULL)>0 THEN count(*) FILTER(WHERE {k}) END {k}_observed_total"])
  db.execute("CREATE TEMP TABLE daily_agg AS SELECT trade_date,count(*) observed_stock_days,count(DISTINCT stock_code) stocks,count(*) FILTER(WHERE price_reason='valid') valid_reference_rows,count(*) FILTER(WHERE scenario_count>1) ambiguous_st_rows,count(*) FILTER(WHERE event_rule_conflict) event_rule_conflicts,"+','.join(metrics)+' FROM target GROUP BY trade_date')
  db.execute("CREATE TEMP TABLE daily AS SELECT c.trade_date,d.* EXCLUDE(trade_date) FROM cal c LEFT JOIN daily_agg d USING(trade_date) WHERE c.trade_date BETWEEN ? AND ? ORDER BY c.trade_date",[start,end])
  for ext in ('parquet','csv'):db.execute('COPY daily TO ? ('+('FORMAT PARQUET,COMPRESSION ZSTD' if ext=='parquet' else 'FORMAT CSV,HEADER')+')',[str(out/('daily.'+ext))])
  quality=rows(db,'SELECT year(trade_date) AS year,candidate_reason,count(*) stock_days FROM target GROUP BY ALL ORDER BY ALL');save(out/'unknown_reasons.json',quality)
  save(out/'source_coverage.json',rows(db,'SELECT year(trade_date) AS year,price_source,count(*) stock_days,count(DISTINCT stock_code) stocks,count(DISTINCT trade_date) AS days FROM target GROUP BY ALL ORDER BY ALL'))
  save(out/'catalogue_coverage.json',rows(db,"""SELECT year(c.trade_date) AS year,count(*) catalogue_expected_stock_days,
   count(*) FILTER(WHERE t.stock_code IS NOT NULL) observed_catalogue_stock_days,
   count(*) FILTER(WHERE t.stock_code IS NULL) missing_catalogue_stock_days,
   count(DISTINCT s.stock_code) catalogue_stocks
   FROM cal c JOIN src.stocks s ON c.trade_date>=s.listing_date AND (s.delisting_date IS NULL OR c.trade_date<=s.delisting_date)
   LEFT JOIN target t ON t.stock_code=s.stock_code AND t.trade_date=c.trade_date
   WHERE c.trade_date BETWEEN ? AND ? AND regexp_full_match(s.stock_code,'(60|68)[0-9]{4}\\.SH|(00|30)[0-9]{4}\\.SZ')
   GROUP BY 1 ORDER BY 1""",[start,end]))
  years=[]
  for y in (2024,2025,2026):
   q=rows(db,f"SELECT count(*) observed_stock_days,count(DISTINCT stock_code) stocks,count(*) FILTER(WHERE price_reason='valid') valid_reference_rows,count(*) FILTER(WHERE lifecycle_status<>'verified') lifecycle_unverified_rows FROM target WHERE year(trade_date)={y}")[0]
   q.update(rows(db,f"SELECT count(*) calendar_days,count(*) FILTER(WHERE observed_stock_days>0) observed_days,min(trade_date) first_date,max(trade_date) last_date,"+','.join(f"count(*) FILTER(WHERE {k}_assessable>0) {k}_usable_days,sum({k}_confirmed) {k}_confirmed,sum({k}_candidate) {k}_candidate,sum({k}_unknown) {k}_unknown,sum({k}_scenario_lower) {k}_scenario_lower" for k in ('up','down','broken'))+f" FROM daily WHERE year(trade_date)={y}")[0]);years.append({'year':y,**q})
  # Independent rule-only comparison. No absent event is used as a negative label.
  db.execute("""CREATE TEMP TABLE comparisons AS SELECT e.provider,e.limit_type,e.stock_code,e.trade_date,e.raw_id,e.consecutive_limit_days,e.limit_price supplier_limit_price,
   CASE WHEN e.limit_price>0 AND f.compatible_scenarios>0 THEN (SELECT min(abs(x.upper_price-e.limit_price)) FROM unnest(f.scenarios) z(x) WHERE x.compatible) END supplier_upper_price_error,
   f.price_source,f.reference_price,f.reference_basis,f.identity_status,f.candidate_reason,f.scenarios,
   CASE e.limit_type WHEN 'up' THEN f.up_candidate WHEN 'down' THEN f.down_candidate WHEN 'broken' THEN f.broken_candidate END candidate_hit,
   CASE WHEN f.stock_code IS NULL THEN 'daily_missing' WHEN candidate_hit IS NULL THEN 'unknown' WHEN candidate_hit THEN 'consistent_candidate' ELSE 'conflict' END comparison,
   CASE WHEN e.trade_date>=DATE '2024-01-01' THEN 'target_period' ELSE '2020_method_check_not_target_validation' END period
   FROM events_all e LEFT JOIN all_facts f USING(stock_code,trade_date)
   WHERE e.trade_date>=DATE '2024-01-01' OR strftime(e.trade_date,'%Y-%m') IN ('2020-01','2020-06','2020-12')""")
  db.execute('COPY comparisons TO ? (FORMAT PARQUET)',[str(out/'event_comparisons.parquet')])
  save(out/'price_error_summary.json',rows(db,"SELECT provider,limit_type,count(supplier_limit_price) quoted_price_rows,count(supplier_upper_price_error) comparable_rows,avg(supplier_upper_price_error) mean_absolute_price_error,max(supplier_upper_price_error) max_price_error,count(*) FILTER(WHERE supplier_upper_price_error>.000001) different_rows FROM comparisons GROUP BY ALL ORDER BY ALL"))
  save(out/'validation_summary.json',rows(db,'SELECT period,provider,limit_type,comparison,count(*) records FROM comparisons GROUP BY ALL ORDER BY ALL'))
  save(out/'conflicts.json',rows(db,"SELECT * FROM comparisons WHERE comparison='conflict' ORDER BY trade_date,stock_code,provider,limit_type"))
  save(out/'validation_samples.json',rows(db,"SELECT * FROM comparisons QUALIFY row_number() OVER(PARTITION BY provider,limit_type,comparison,year(trade_date) ORDER BY trade_date,stock_code)<=10 ORDER BY provider,trade_date,stock_code"))
  old=rows(db,"SELECT year(trade_date) AS year,count(*) AS days,count(*) FILTER(WHERE confirmed>0) confirmed_positive_days,count(*) FILTER(WHERE reconstructed_candidate>0) candidate_positive_days,sum(confirmed) confirmed,sum(reconstructed_candidate) candidate,sum(unknown_observed) unknown FROM read_parquet(?) WHERE trade_date>=DATE '2024-01-01' GROUP BY 1 ORDER BY 1",[str(paths['H']/'daily.parquet')]);save(out/'before_H_coverage.json',old)
  assert len(rows(db,'SELECT * FROM daily'))==len([d for d in days if d>=start])
  assert active_signature()==signature
  summary={'version':VERSION,'candidate_id':key,'parents':versions,'years':years,'official_certification':False,'full_market_coverage':None,'output':str(out),'scope':'沪深A股观察样本；不补造缺失股票日','active_unchanged':True}
  save(out/'summary.json',summary)
  save(out/'manifest.json',{'version':VERSION,'candidate_only':True,'inputs_sha256':sha(out/'inputs.json'),'files':{p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()}})
  print(json.dumps(summary,ensure_ascii=False,default=str),flush=True)
 finally:db.close()
 return out
if __name__=='__main__':build()
