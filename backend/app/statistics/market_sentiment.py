"""Read-only E4 facade: frozen full-sample results, existing calculators for scopes."""
from collections import defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path
import calendar,json,hashlib,copy
import duckdb
from app.research.queries import DEFAULT_ROOT
from app.statistics.history_limit_queries import dataset,rows
from app.statistics.stock_scope import membership
from app.statistics.market_breadth import daily_sql,validate_daily
from app.analytics.direction_signals import daily_signals,breadth_comparison
from app.analytics.matched_cohort_breadth import matched_breadth
from app.analytics.limit_structure import pool,promotion,structure
from scripts.build_market_breadth import calculate
from scripts.build_direction_signals import open_profiles,profiles
from scripts.build_matched_cohort_breadth import detailed_profiles

VERSION='market-sentiment-api-v1'
ROOT=Path(DEFAULT_ROOT)
LAYERS={'U':'research/unified','H':'statistics/history_limits','E1':'statistics/market_breadth','E2':'statistics/limit_structure','E3':'statistics/direction_signals','D1':'statistics/matched_cohort_breadth'}

def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def active_signature():
 return tuple((k,str(ROOT/v/'manifest.json'),digest(ROOT/v/'manifest.json')) for k,v in LAYERS.items())+(('C',str(ROOT/'statistics/history_limits/classification_manifest.json'),digest(ROOT/'statistics/history_limits/classification_manifest.json')),)

@lru_cache(maxsize=2)
def releases(signature):
 paths={};versions={}
 for key,file,h in signature:
  p=Path(file);m=json.loads(p.read_text())
  if digest(p)!=h:raise ValueError('Active release changed during request')
  base=p.parent;target=(base/m['dataset']).resolve()
  if not target.is_relative_to(base) or 'release_candidates' in str(target):raise ValueError('Invalid active release directory')
  paths[key]=target;versions[key]={'id':target.name,'path':str(target),'manifest_sha256':h,'rule_version':m.get('version')}
  if m.get('summary_sha256') and digest(target/'summary.json')!=m['summary_sha256']:raise ValueError('Summary checksum mismatch')
  summary=json.loads((target/'summary.json').read_text())
  for name,hsh in summary.get('files',m.get('files',{})).items():
   if digest(target/name)!=hsh:raise ValueError('Snapshot checksum mismatch')
 for k in ('E1','E2','E3','D1'):
  inp=json.loads((paths[k]/'inputs.json').read_text())
  if inp['source_snapshot']!=str(paths['U']):raise ValueError('Mixed U parent')
  for field,parent in [('history_snapshot','H'),('breadth_snapshot','E1'),('limit_snapshot','E2'),('e2_snapshot','E2'),('old_e3_snapshot','E3')]:
   if field in inp and inp[field]!=str(paths[parent]):raise ValueError('Mixed '+field)
 h=json.loads((paths['H']/'summary.json').read_text());c=json.loads((paths['C']/'summary.json').read_text())
 if h['source_snapshot']!=str(paths['U']) or c['source_snapshot']!=str(paths['H']) or c['unified_snapshot']!=str(paths['U']):raise ValueError('Mixed H/C parent')
 db=duckdb.connect();days=[str(x[0]) for x in db.execute('SELECT trade_date FROM read_parquet(?) ORDER BY trade_date',[str(paths['H']/'daily.parquet')]).fetchall()];db.close()
 if len(days)!=len(set(days)):raise ValueError('Duplicate trading dates')
 return paths,versions,days


def compact_signal(s):
 keys=('id','name','layer','direction','value','previous_value','delta','unit','delta_unit','reason','reason_codes','status','comparison_status','source','snapshot_id','input_snapshots','rule_version','eligible_for_formal_counts')
 r={k:s.get(k) for k in keys};r['basis']={k:v for k,v in s.get('basis',{}).items() if k in ('formula','current','previous','cohort_status')};return r

def compact_d1(r):
 return {k:v for k,v in r.items() if k not in ('source_changed_codes','untrusted_common_codes')}

@lru_cache(maxsize=24)
def frozen_month(signature,month):
 p,_,_=releases(signature);db=duckdb.connect()
 e1={str(r['trade_date']):r for r in rows(db,'SELECT * FROM read_parquet(?)',[str(p['E1']/(month+'.parquet'))])};db.close()
 e3={r['trade_date']:[compact_signal(s) for s in r['signals']] for r in json.loads((p['E3']/(month+'.json')).read_text())}
 d1={r['trade_date']:compact_d1(r['matched_cohort_breadth']) for r in json.loads((p['D1']/(month+'.json')).read_text())}
 return e1,e3,d1

@lru_cache(maxsize=2)
def frozen_limits(signature):
 p,_,_=releases(signature)
 return {r['trade_date']:r for r in json.loads((p['E2']/'daily.json').read_text())}


def eligible_view(db,meta,st,ipo):
 membership(db,meta,st,ipo)
 # Materialize just keys/attributes; no raw or archived table changes.
 db.execute('CREATE TEMP TABLE selected_members AS SELECT * FROM scope_members')
 db.execute('CREATE TEMP VIEW selected_positives AS SELECT p.* FROM positives p JOIN selected_members m USING(stock_code,trade_date) WHERE m.eligible')

@lru_cache(maxsize=12)
def historical_month(signature,month,st,ipo):
 p,_,_=releases(signature);y,m=map(int,month.split('-'));start=f'{month}-01';end=f'{month}-{calendar.monthrange(y,m)[1]}'
 with dataset() as (db,meta):
  if Path(meta['snapshot'])!=p['H']:raise ValueError('H changed')
  eligible_view(db,meta,st,ipo)
  stats=rows(db,"""SELECT d.trade_date,count(*) observed,count(*) FILTER(WHERE m.eligible) eligible,
   count(*) FILTER(WHERE m.eligible AND d.up AND d.status='confirmed') confirmed,
   count(*) FILTER(WHERE m.eligible AND d.up AND d.status='reconstructed_candidate') candidate,
   count(*) FILTER(WHERE m.eligible AND d.up IS NOT NULL) assessable,
   count(*) FILTER(WHERE m.eligible AND d.up IS NULL) unknown,
   count(*) FILTER(WHERE m.st_status='unknown') st_unknown,count(*) FILTER(WHERE m.ipo_phase='unknown') ipo_unknown
   FROM days d JOIN selected_members m USING(stock_code,trade_date) WHERE d.trade_date BETWEEN ? AND ? GROUP BY 1""",[start,end])
  ladder={str(r['trade_date']):r for r in rows(db,"""SELECT trade_date,count(*) FILTER(WHERE height=1) first,
   count(*) FILTER(WHERE height=2) "second",count(*) FILTER(WHERE height>=3) third_plus,max(height) max_height
   FROM selected_positives WHERE trade_date BETWEEN ? AND ? GROUP BY 1""",[start,end])}
  return {str(r['trade_date']):{**r,'ladder':ladder.get(str(r['trade_date']),{'first':0,'second':0,'third_plus':0,'max_height':None})} for r in stats}

@lru_cache(maxsize=8)
def scoped_month(signature,month,st,ipo):
 p,_,days=releases(signature);month_days=[d for d in days if d.startswith(month)];first=days.index(month_days[0]);start=days[max(0,first-2)];end=month_days[-1]
 bi=json.loads((p['E1']/'inputs.json').read_text());life=json.loads(Path(bi['lifecycle_report']).read_text())
 db=open_profiles(p['U'],p['H']/'recovered_references.parquet',life)
 try:
  calculate(db,start,end)
  # Reuse exactly the same day membership as H counts, events and lists.
  db.read_parquet(str(p['H']/'days.parquet')).create_view('days')
  membership(db,{'source_snapshot':str(p['U'])},st,ipo)
  db.execute('CREATE TEMP TABLE permitted AS SELECT * FROM scope_members WHERE eligible AND trade_date BETWEEN ? AND ?',[start,end])
  db.execute('DELETE FROM evaluated WHERE NOT EXISTS(SELECT 1 FROM permitted m WHERE m.stock_code=evaluated.stock_code AND m.trade_date=evaluated.trade_date)')
  breadth={str(r['trade_date']):r for r in rows(db,daily_sql())};validate_daily(list(breadth.values()))
  simple=profiles(db);detail=detailed_profiles(db,p['U'].name,bi.get('version','observed-market-breadth-v1'))
  facts=defaultdict(dict)
  for r in rows(db,'SELECT e.stock_code,e.trade_date,e.identity_status,e.price_reason,e.trade_status,u.event_conflict,u.is_limit_up,u.event_source,u.event_raw_id,e.reference_basis FROM evaluated e JOIN frozen.stock_daily u USING(stock_code,trade_date)'):
   facts[str(r['trade_date'])][r['stock_code']]=r
  members=defaultdict(set)
  for d,c in db.execute('SELECT trade_date,stock_code FROM permitted').fetchall():members[str(d)].add(c)
  events=defaultdict(list)
  for r in rows(db,"SELECT * FROM frozen.events WHERE provider='akshare' AND trade_date BETWEEN ? AND ?",[start,end]):events[str(r['trade_date']),r['limit_type']].append(r)
  cov={(str(r['trade_date']),r['dataset']):r for r in rows(db,"SELECT * FROM read_parquet(?) WHERE provider='akshare'",[str(p['U']/'pg_data_coverage.parquet')])}
  pools={}
  for d in breadth:
   for kind in ('up','broken','down'):
    q=pool('akshare',date.fromisoformat(d),kind,events[d,kind],cov.get((d,kind)),scope_verified=False)
    if q['records'] is not None:q['records']=[e for e in q['records'] if e['stock_code'] in members[d]]
    q['scope']+=f';exclude_st={st};exclude_ipo={ipo}';pools[d,kind]=q
  limits={};signals={};matched={};ids={k:v.name for k,v in p.items()}
  for d in breadth:
   ix=days.index(d);prev=days[ix-1] if ix else None
   q={'trade_date':d,'previous_trading_day':prev,'provider':'akshare',**structure(pools[d,'up'],pools[d,'broken'],pools[d,'down'],facts[d])}
   for level,key in ((1,'promotion_1to2'),(2,'promotion_2to3')):
    prior=pools.get((prev,'up'),pool('akshare',date.fromisoformat(prev) if prev else None,'up',[],None))
    # Scope both dates BEFORE fixing the queue, then never drop unknown outcomes.
    prior=copy.deepcopy(prior)
    if prior['records'] is not None:prior['records']=[e for e in prior['records'] if e['stock_code'] in members[d]]
    q[key]=promotion(prior,pools[d,'up'],facts[prev],facts[d],level,date.fromisoformat(prev) if prev else None)
   limits[d]=q
   if d not in month_days:continue
   sid='query:'+p['E3'].name+f':{int(st)}{int(ipo)}'
   result=daily_signals(d,prev,breadth[d],breadth.get(prev),q,limits.get(prev),breadth_comparison(simple.get(prev),simple.get(d)),snapshot_id=sid,input_snapshots=ids)
   signals[d]=[compact_signal(s) for s in result['signals']]
   for s in signals[d]:
    s['stock_scope']={'exclude_st':st,'exclude_ipo':ipo}
    if isinstance(s.get('source'),dict):s['source']={**s['source'],'stock_scope':s['stock_scope'],'includes_ST_and_unknown':not st}
   matched[d]=compact_d1(matched_breadth(d,prev,detail.get(prev),detail.get(d),snapshot_id='query:'+p['D1'].name+f':{int(st)}{int(ipo)}',input_snapshots=ids))
   matched[d]['stock_scope']={'exclude_st':st,'exclude_ipo':ipo}
   matched[d]['source']={**matched[d]['source'],'stock_scope':matched[d]['stock_scope']}
  return breadth,signals,matched,limits
 finally:db.close()


def metric(value,quality,layer,versions,source,reason=None):
 return dict(value=value,quality='unknown' if value is None else quality,source=source,snapshot=versions[layer]['id'],rule_version=versions[layer]['rule_version'],reason=reason)

def query(trade_date=None,start=None,end=None,exclude_st=False,exclude_ipo=False):
 signature=active_signature();paths,versions,days=releases(signature)
 if trade_date:
  if start or end:raise ValueError('trade_date 与 start/end 不能同时使用')
  start=end=str(trade_date)
 else:start=str(start or days[-1]);end=str(end or start)
 if start>end or start<days[0] or end>days[-1]:raise ValueError('日期范围超出已安装快照')
 selected=[d for d in days if start<=d<=end]
 if trade_date and not selected:raise ValueError('所选日期不是已覆盖交易日')
 if (exclude_st or exclude_ipo) and (date.fromisoformat(end)-date.fromisoformat(start)).days>366:raise ValueError('筛选范围单次最多366天；可按年份查询全部历史')
 result=[];filtered=exclude_st or exclude_ipo
 for month in sorted({d[:7] for d in selected}):
  history=historical_month(signature,month,exclude_st,exclude_ipo)
  if filtered:b,sig,common,limits=scoped_month(signature,month,exclude_st,exclude_ipo)
  else:b,sig,common=frozen_month(signature,month);limits=frozen_limits(signature)
  for day in [d for d in selected if d.startswith(month)]:
   row=b[day];h=history.get(day,{});e=limits[day];diag=e['supplier_diagnostic'];valid=bool(row['price_valid_count']);assessable=bool(h.get('assessable'))
   def m(v,q,l,src,reason=None):return metric(v,q,l,versions,src,reason)
   breadth={k:m(row[k],'confirmed','E1','已验证参考价的可观察样本；非全市场',None if valid else row['status']) for k in ('up','down','flat','advancing_ratio','median_return')}
   counts={k:row[k] for k in ('observed_count','price_valid_count','price_unknown_count','st_unknown_count','coverage_ratio','universe_status')}
   mixed='candidate' if h.get('candidate') else 'confirmed'
   limit={'up':m((h['confirmed']+h['candidate']) if assessable else None,mixed,'H','H 已确认+重建候选'),
    'confirmed':m(h.get('confirmed') if assessable else None,'confirmed','H','已有独立日期事件或限价证据，非官方全量认证'),
    'candidate':m(h.get('candidate') if assessable else None,'candidate','H','历史规则重建'),
    'down':m(diag.get('limit_down_count'),'supplier_diagnostic','E2','AKShare 收录跌停池',e['pools']['down'].get('reason')),
    'broken':m(diag.get('broken_limit_count'),'supplier_diagnostic','E2','AKShare 收录炸板池',e['pools']['broken'].get('reason')),
    'broken_rate':m(diag.get('broken_limit_rate'),'supplier_diagnostic','E2','炸板 / (供应商涨停+炸板)',None if diag.get('broken_limit_rate') is not None else '供应商池缺失、分母为0或来源不兼容')}
   ladder={k:m(h.get('ladder',{}).get(k) if assessable else None,mixed,'H','同范围的已观察连续涨停高度') for k in ('first','second','third_plus','max_height')}
   promotions={}
   for key in ('promotion_1to2','promotion_2to3'):
    q=e[key];dq=q['supplier_diagnostic'];promotions[key]={**m(dq['rate'],'supplier_diagnostic','E2','固定上一真实交易日股票队列的供应商诊断'), 'numerator':dq.get('promoted_count'),'denominator':dq.get('fixed_denominator'),'strict_value':q.get('rate'),'strict_quality':'confirmed' if q.get('rate') is not None else 'unknown','reason_codes':q.get('cohort_reasons'), 'cohort_codes':dq.get('cohort_codes',[])}
   signal_rows=copy.deepcopy(sig[day]);d1=common[day]
   signal_rows.append({**d1,'layer':'matched_cohort','quality':'confirmed' if d1.get('calculable') else 'unknown'})
   result.append(dict(trade_date=day,previous_trading_day=e.get('previous_trading_day'),breadth=breadth,breadth_quality=counts,limits=limit,ladder=ladder,promotions=promotions,signals=signal_rows,d1=d1,scope_quality={k:h.get(k) for k in ('observed','eligible','st_unknown','ipo_unknown')}))
 if active_signature()!=signature:raise ValueError('Active pointers changed during response; retry after maintenance')
 return {'data':result,'meta':{'version':VERSION,'snapshots':versions,'stock_scope':{'exclude_st':exclude_st,'exclude_ipo':exclude_ipo},'calculation':'existing_rules_scoped_recalculation' if filtered else 'accepted_frozen_E1_E2_E3_D1','days':len(result),'available_days':len(days),'start':days[0],'end':days[-1],'coverage_ratio':None,'warning':'观察样本研究；候选与供应商诊断不是官方确认；非预测，无综合分数。'}}


def dates():
 signature=active_signature();_,versions,days=releases(signature);return {'data':days,'meta':{'snapshots':versions,'version':VERSION}}


def stocks(trade_date,kind='up',exclude_st=False,exclude_ipo=False,page=1,page_size=50):
 signature=active_signature();p,v,days=releases(signature);day=str(trade_date)
 if day not in days:raise ValueError('不是已覆盖交易日')
 if kind not in ('up','first','second','third_plus','broken','down','advancing','declining','flat','promotion_1to2','promotion_2to3'):raise ValueError('Unknown stock list kind')
 if kind.startswith('promotion_'):
  limit=scoped_month(signature,day[:7],exclude_st,exclude_ipo)[3][day] if exclude_st or exclude_ipo else frozen_limits(signature)[day]
  q=limit[kind]['supplier_diagnostic'];records=q['matches']
  if active_signature()!=signature:raise ValueError('Active version changed')
  return {'data':records[(page-1)*page_size:page*page_size],'meta':{'total':q['fixed_denominator'],'quality':'supplier_diagnostic' if q['fixed_denominator'] is not None else 'unknown','numerator':q['promoted_count'],'snapshots':v,'page':page,'page_size':page_size}}
 with dataset() as (db,meta):
  eligible_view(db,meta,exclude_st,exclude_ipo)
  if kind in ('up','first','second','third_plus'):
   condition={'up':'TRUE','first':'height=1','second':'height=2','third_plus':'height>=3'}[kind]
   sql='SELECT * FROM selected_positives WHERE trade_date=? AND '+condition;args=[day];quality='confirmed_and_candidate'
  elif kind in ('broken','down'):
   available=frozen_limits(signature)[day]['pools'][kind]['status']=='supplier_observed'
   if not available:return {'data':[],'meta':{'total':None,'quality':'unknown','reason':'supplier_pool_unavailable','snapshots':v}}
   db.read_parquet(str(p['U']/'events.parquet')).create_view('scope_events')
   sql="SELECT e.* FROM scope_events e JOIN selected_members m USING(stock_code,trade_date) WHERE m.eligible AND e.trade_date=? AND e.limit_type=? AND e.provider='akshare'";args=[day,kind];quality='supplier_diagnostic'
  else:
   bi=json.loads((p['E1']/'inputs.json').read_text());edb=open_profiles(p['U'],p['H']/'recovered_references.parquet',json.loads(Path(bi['lifecycle_report']).read_text()))
   try:
    calculate(edb,day,day);records=rows(edb,"SELECT stock_code,trade_date,close,reference_price,daily_return,price_source,identity_status FROM evaluated WHERE in_scope AND price_reason='valid' AND daily_return "+{'advancing':'>0','declining':'<0','flat':'=0'}[kind])
   finally:edb.close()
   permitted={r[0] for r in db.execute('SELECT stock_code FROM selected_members WHERE trade_date=? AND eligible',[day]).fetchall()};records=[r for r in records if r['stock_code'] in permitted];records.sort(key=lambda r:r['stock_code'])
   if active_signature()!=signature:raise ValueError('Active version changed')
   return {'data':records[(page-1)*page_size:page*page_size],'meta':{'total':len(records),'quality':'confirmed','source':'E1 existing calculator','snapshots':v,'page':page,'page_size':page_size}}
  total=db.execute('SELECT count(*) FROM ('+sql+')',args).fetchall()[0][0];data=rows(db,sql+' ORDER BY stock_code LIMIT ? OFFSET ?',args+[page_size,(page-1)*page_size])
 if active_signature()!=signature:raise ValueError('Active version changed')
 return {'data':data,'meta':{'total':total,'quality':quality,'snapshots':v,'page':page,'page_size':page_size}}
