"""First frozen-data timing experiment. Read existing candidates; no active writes."""
from pathlib import Path
import json,hashlib,math
import numpy as np
import pandas as pd
import duckdb
from scipy.stats import rankdata

OUT=Path('data/timing_experiment1')
METRICS=['up_count','down_count','broken_rate','promotion_1to2','promotion_2to3','advancing_ratio']
VARIANTS=['candidate_include_unknown','candidate_exclude_unknown_st','candidate_scenario_conservative','confirmed_only']
TARGETS=['future_mean_intraday_breadth','future_any_intraday_breadth_below_30pct','future_mean_reference_breadth']
COMBOS={'up_high_and_broken_low':['up_count_high','broken_rate_low'],'breadth_high_and_down_low':['advancing_ratio_high','down_count_low']}

def save(name,x):
 def cast(v):
  if isinstance(v,(np.integer,np.floating)):return v.item()
  return str(v)
 (OUT/name).write_text(json.dumps(x,ensure_ascii=False,indent=2,default=cast,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def records(db,sql,args=None):
 c=db.execute(sql,args or []);return [dict(zip([x[0] for x in c.description],r)) for r in c.fetchall()]

def features():
 protocol=json.loads((OUT/'protocol.json').read_text());p=Path(protocol['snapshot']);manifest=json.loads((p/'manifest.json').read_text())
 assert all(sha(p/f)==h for f,h in manifest['files'].items())
 parents=json.loads((p/'inputs.json').read_text())['parents'];u=Path(parents['U']['path'])
 db=duckdb.connect(config={'threads':2,'memory_limit':'2GB'});db.execute('ATTACH ? AS u (READ_ONLY)',[str(u/'research.duckdb')])
 db.read_parquet(str(p/'20??-??.parquet')).create_view('saved')
 db.execute("CREATE TEMP TABLE calendar AS SELECT trade_date,row_number() OVER(ORDER BY trade_date) idx,lag(trade_date) OVER(ORDER BY trade_date) prev FROM u.calendar")
 db.execute("""CREATE TEMP TABLE restored AS SELECT s.stock_code,s.trade_date,
 bool_or(s.high<=x.upper_price+.000001 AND s.low>=x.lower_price-.000001) compatible,
 bool_or(s.high<=x.upper_price+.000001 AND s.low>=x.lower_price-.000001 AND abs(s.close-x.upper_price)<.000001) restored_up,
 bool_or(s.high<=x.upper_price+.000001 AND s.low>=x.lower_price-.000001 AND abs(s.close-x.lower_price)<.000001) restored_down,
 bool_or(s.high<=x.upper_price+.000001 AND s.low>=x.lower_price-.000001 AND abs(s.high-x.upper_price)<.000001 AND s.close<x.upper_price-.000001) restored_broken
 FROM saved s JOIN calendar c USING(trade_date) JOIN u.stock_daily prior ON prior.stock_code=s.stock_code AND prior.trade_date=c.prev,
 unnest(s.scenarios) z(x)
 WHERE s.trade_date>=DATE '2024-01-01' AND s.candidate_reason='delisting_special_unverified' AND s.price_reason='valid'
 AND prior.trade_status IS DISTINCT FROM FALSE AND s.stock_code<>'600182.SH'
 AND NOT coalesce(s.event_conflict,false) AND NOT s.event_type_conflict AND NOT s.event_rule_conflict
 GROUP BY s.stock_code,s.trade_date""")
 db.execute("""CREATE TEMP TABLE base AS SELECT s.*,c.idx,coalesce(r.compatible,false) neutralize_future_delisting_window,
 CASE WHEN s.up_status='confirmed' THEN s.up WHEN r.compatible THEN r.restored_up ELSE s.up END study_up,
 CASE WHEN s.down_status='confirmed' THEN s.down WHEN r.compatible THEN r.restored_down ELSE s.down END study_down,
 CASE WHEN s.broken_status='confirmed' THEN s.broken WHEN r.compatible THEN r.restored_broken ELSE s.broken END study_broken
 FROM saved s JOIN calendar c USING(trade_date) LEFT JOIN restored r USING(stock_code,trade_date)
 WHERE s.trade_date>=DATE '2024-01-01'""")
 days=pd.DatetimeIndex([r[0] for r in db.execute("SELECT trade_date FROM calendar WHERE trade_date BETWEEN DATE '2024-01-01' AND (SELECT max(trade_date) FROM base) ORDER BY 1").fetchall()])
 save('temporal_audit.json',{'neutralized_future_delisting_rows':db.execute('SELECT count(*) FROM base WHERE neutralize_future_delisting_window').fetchall()[0][0], 'affected':records(db,'SELECT stock_code,min(trade_date) AS start,max(trade_date) AS end,count(*) AS rows FROM base WHERE neutralize_future_delisting_window GROUP BY 1 ORDER BY 1'),'candidate_original_unchanged':True,'pit_certified':False,'reason':'historical source corrections, ST known-at and exact release times are unavailable; no actual trade profit claim'})
 quality=records(db,"SELECT trade_date,count(*) observed,count(*) FILTER(WHERE price_reason='valid') price_valid,count(*) FILTER(WHERE identity_status='unknown') st_unknown,"+','.join(f"count(*) FILTER(WHERE {k}_status='{status}') {k}_{status}_rows" for k in ('up','down','broken') for status in ('confirmed','candidate','unknown'))+' FROM base GROUP BY 1 ORDER BY 1')
 pd.DataFrame(quality).to_csv(OUT/'daily_quality.csv',index=False)
 outcomes=db.execute("""SELECT trade_date,count(*) observed,count(*) FILTER(WHERE price_reason='valid') valid,
 count(*) FILTER(WHERE price_reason='valid' AND close>open)*1.0/nullif(count(*) FILTER(WHERE price_reason='valid'),0) intraday_breadth,
 count(*) FILTER(WHERE price_reason='valid' AND close>reference_price)*1.0/nullif(count(*) FILTER(WHERE price_reason='valid'),0) reference_breadth
 FROM base GROUP BY 1 ORDER BY 1""").df().set_index('trade_date').reindex(days)
 for c in ('intraday_breadth','reference_breadth'):outcomes.loc[(outcomes.valid<1000)|(outcomes.valid/outcomes.observed<.95),c]=np.nan
 outcomes.to_csv(OUT/'daily_outcomes.csv',index_label='trade_date')
 frames={}
 for variant in VARIANTS:
  flags=[]
  for k in ('up','down','broken'):
   v='study_'+k
   if variant=='candidate_exclude_unknown_st':v=f"CASE WHEN identity_status<>'unknown' THEN study_{k} END"
   if variant=='candidate_scenario_conservative':v=f"CASE WHEN {k}_status='confirmed' THEN {k} WHEN study_{k} AND (NOT {k}_all_scenarios OR neutralize_future_delisting_window) THEN NULL ELSE study_{k} END"
   if variant=='confirmed_only':v=f"CASE WHEN {k}_status='confirmed' THEN {k} END"
   flags.append(v+f' flag_{k}')
  db.execute('CREATE OR REPLACE TEMP TABLE scoped AS SELECT *, '+','.join(flags)+' FROM base')
  member="identity_status<>'unknown'" if variant=='candidate_exclude_unknown_st' else 'TRUE'
  scope_sql=f"""SELECT trade_date,count(*) FILTER(WHERE {member}) observed,
    count(*) FILTER(WHERE {member} AND price_reason='valid') breadth_valid,
    count(*) FILTER(WHERE {member} AND price_reason='valid' AND close>reference_price)*1.0/nullif(count(*) FILTER(WHERE {member} AND price_reason='valid'),0) advancing_ratio,
    count(*) FILTER(WHERE flag_up IS NOT NULL) up_assessable,count(*) FILTER(WHERE flag_down IS NOT NULL) down_assessable,
    count(*) FILTER(WHERE flag_broken IS NOT NULL) broken_assessable,
    count(*) FILTER(WHERE flag_up) up_count,count(*) FILTER(WHERE flag_down) down_count,count(*) FILTER(WHERE flag_broken) broken_count,
    count(*) FILTER(WHERE event_rule_conflict) event_conflicts
    FROM scoped GROUP BY 1 ORDER BY 1"""
  f=db.execute(scope_sql).df().set_index('trade_date').reindex(days)
  for k in ('up','down','broken'):
   allowed=(f.observed>=1000)&(f[k+'_assessable']/f.observed>=.95)
   if variant=='confirmed_only':allowed=f[k+'_assessable']>0 # positive-observation count, not full pool coverage
   f.loc[~allowed,k+'_count']=np.nan
  f['broken_rate']=f.broken_count/(f.up_count+f.broken_count).replace(0,np.nan)
  f.loc[(f.breadth_valid<1000)|(f.breadth_valid/f.observed<.95),'advancing_ratio']=np.nan
  # Runs can start only after a known negative on the preceding real session.
  db.execute("""CREATE OR REPLACE TEMP TABLE state AS WITH prev AS (
    SELECT stock_code,trade_date,idx,flag_up,lag(flag_up) OVER w prior_up,lag(idx) OVER w prior_idx
    FROM scoped WINDOW w AS(PARTITION BY stock_code ORDER BY idx)
   ), runs AS (
    SELECT *,sum(CASE WHEN prior_idx IS DISTINCT FROM idx-1 OR prior_up IS DISTINCT FROM TRUE OR flag_up IS DISTINCT FROM TRUE THEN 1 ELSE 0 END)
      OVER(PARTITION BY stock_code ORDER BY idx) segment FROM prev
   ) SELECT *,CASE WHEN flag_up IS TRUE AND first_value(prior_up) OVER w IS FALSE AND first_value(prior_idx) OVER w=first_value(idx) OVER w-1
      THEN row_number() OVER w END exact_height
   FROM runs WINDOW w AS(PARTITION BY stock_code,segment ORDER BY idx)""")
  # Keep missing and disqualified today in yesterday's denominator, never shrink it.
  for level in (1,2):
   q=db.execute("""SELECT c.trade_date,count(*) denominator,count(*) FILTER(WHERE today.flag_up IS TRUE) numerator,
    count(*) FILTER(WHERE today.flag_up IS NULL) unresolved
    FROM state yesterday JOIN calendar c ON c.idx=yesterday.idx+1
    LEFT JOIN state today ON today.stock_code=yesterday.stock_code AND today.idx=c.idx
    WHERE yesterday.exact_height=? AND c.trade_date<=? GROUP BY 1 ORDER BY 1""",[level,days[-1].date()]).df().set_index('trade_date').reindex(days)
   for col in ('denominator','numerator','unresolved'):f[f'p{level}_{col}']=q[col]
   col=f'promotion_{level}to{level+1}';f[col]=q.numerator/q.denominator
   f.loc[(q.unresolved!=0)|(q.denominator<(5 if level==1 else 3))|(f.observed<1000),col]=np.nan
  frames[variant]=f
  f.to_csv(OUT/f'features_{variant}.csv',index_label='trade_date')
 # Auditable 2024/2025 special/IPO/ST/price-boundary samples, not chosen by future returns.
 sample=records(db,"""SELECT stock_code,trade_date,identity_status,candidate_reason,listing_date,listing_session,open,high,low,close,reference_price,scenarios,up_status,down_status,broken_status,up,down,broken
 FROM base WHERE year(trade_date) IN (2024,2025) AND (candidate_reason<>'regular_session_rule_candidate' OR up OR down OR broken OR identity_status='confirmed_st')
 QUALIFY row_number() OVER(PARTITION BY year(trade_date),candidate_reason,identity_status,coalesce(up,false),coalesce(down,false),coalesce(broken,false) ORDER BY trade_date,stock_code)<=3 ORDER BY trade_date,stock_code""");save('audited_stock_day_samples.json',sample)
 db.close();save('input_manifest.json',{'candidate_manifest_sha256':sha(p/'manifest.json'),'protocol_sha256':sha(OUT/'protocol.json'),'source':str(p),'parents':parents})
 return days,frames,outcomes


def future_labels(outcomes,h):
 n=len(outcomes);a=outcomes.intraday_breadth.to_numpy();b=outcomes.reference_breadth.to_numpy();result={k:np.full(n,np.nan) for k in TARGETS};ends=np.full(n,np.datetime64('NaT'),dtype='datetime64[ns]')
 for t in range(n-h):
  av=a[t+1:t+h+1];bv=b[t+1:t+h+1];ends[t]=outcomes.index[t+h].to_datetime64()
  if np.isfinite(av).all():result[TARGETS[0]][t]=av.mean();result[TARGETS[1]][t]=float((av<.30).any())
  if np.isfinite(bv).all():result[TARGETS[2]][t]=bv.mean()
 return result,ends


def fit_signals(frame,dates):
 train=(dates.year<=2025);thresholds={};masks={};eligible={}
 for metric in METRICS:
  x=frame[metric].to_numpy(dtype=float);v=x[train&np.isfinite(x)]
  if len(v)<60:thresholds[metric]={'n':len(v),'q25':None,'q75':None};lo=hi=np.nan
  else:lo,hi=np.quantile(v,[.25,.75]);thresholds[metric]={'n':len(v),'q25':float(lo),'q75':float(hi)}
  for side,value in [('low',lo),('high',hi)]:
   name=metric+'_'+side;eligible[name]=np.isfinite(x)&np.isfinite(value)
   masks[name]=eligible[name]&((x<=value) if side=='low' else (x>=value))
 for name,parts in COMBOS.items():eligible[name]=eligible[parts[0]]&eligible[parts[1]];masks[name]=masks[parts[0]]&masks[parts[1]]
 return thresholds,masks,eligible


def bootstrap_indices(n,replicates=2000,block=10,seed=20261110):
 rng=np.random.default_rng(seed);starts=rng.integers(0,n,size=(replicates,math.ceil(n/block)));return ((starts[:,:,None]+np.arange(block))%n).reshape(replicates,-1)[:,:n]

def mean_diff(y,a,b):
 n1=int(a.sum());n0=int(b.sum())
 if not n1 or not n0:return None
 m1=float(y[a].mean());m0=float(y[b].mean());delta=m1-m0
 # Influence scores with calendar gaps left as zero, for overlapping/nested groups.
 z=np.where(a,(y-m1)/n1,0)-np.where(b,(y-m0)/n0,0);z=np.nan_to_num(z)
 var=float(z@z)
 for lag in range(1,min(10,len(z)-1)+1):var+=2*(1-lag/11)*float(z[lag:]@z[:-lag])
 se=math.sqrt(max(0,var)*len(z)/max(1,len(z)-2))
 pv=math.erfc(abs(delta/se)/math.sqrt(2)) if se>0 else (1.0 if abs(delta)<1e-15 else None)
 return {'selected_n':n1,'baseline_n':n0,'conditional_mean':m1,'baseline_mean':m0,'effect':delta,'hac_se':se,'p':pv}

def ci_diff(y,a,b,idx):
 ya=np.where(a,y,0);yb=np.where(b,y,0);na=a[idx].sum(1);nb=b[idx].sum(1)
 with np.errstate(divide='ignore',invalid='ignore'):ma=ya[idx].sum(1)/na;mb=yb[idx].sum(1)/nb;ds=ma-mb
 good=np.isfinite(ds)
 if good.sum()<len(idx)*.9:return {'ci_low':None,'ci_high':None,'conditional_ci_low':None,'conditional_ci_high':None}
 lo,hi=np.quantile(ds[good],[.025,.975]);ml,mh=np.quantile(ma[good],[.025,.975]);return {'ci_low':float(lo),'ci_high':float(hi),'conditional_ci_low':float(ml),'conditional_ci_high':float(mh)}

def holm(ps,family_size):
 order=np.argsort(ps);out=np.ones(len(ps));prev=0
 for rank,i in enumerate(order):prev=max(prev,min(1,(family_size-rank)*ps[i]));out[i]=prev
 return out


def study(days,frames,outcomes):
 all_thresholds={};results=[];labels={h:future_labels(outcomes,h) for h in (1,3,5)}
 for variant,frame in frames.items():
  thresholds,masks,elig=fit_signals(frame,days);all_thresholds[variant]=thresholds
  for period in ('2024','2025','train_2024_2025','2026'):
   period_mask=(days.year<=2025) if period=='train_2024_2025' else (days.year==int(period));indices=np.where(period_mask)[0];idx=bootstrap_indices(len(indices))
   for h,(targets,ends) in labels.items():
    for target,y_all in targets.items():
     ok=np.isfinite(y_all)
     if period!='2026':ok&=ends<np.datetime64('2026-01-01')
     yy=y_all[indices];valid=ok[indices]
     for name in masks:
      a=masks[name][indices]&valid;b=elig[name][indices]&valid
      base={'variant':variant,'period':period,'horizon':h,'target':target,'kind':'signal','name':name,'trade_return':None}
      r=mean_diff(yy,a,b)
      if r:
       inference=r['selected_n']>=15 and r['baseline_n']>=60 and (b&~a).sum()>=15
       r.update(ci_diff(yy,a,b,idx));r['inference_eligible']=bool(inference)
       if not inference:r['p']=None
       r['conditional_q10']=float(np.quantile(yy[a],.1));r['conditional_median']=float(np.median(yy[a]))
      else:r={'selected_n':int(a.sum()),'baseline_n':int(b.sum()),'inference_eligible':False,'p':None,'reason':'no_training_threshold_or_no_valid_target'}
      results.append({**base,**r})
     # Fixed combination vs each constituent on identical eligible calendars.
     for name,parts in COMBOS.items():
      for part in parts:
       e=elig[name][indices]&valid;a=masks[name][indices]&e;b=masks[part][indices]&e
       r=mean_diff(yy,a,b)
       if r:
        r.update(ci_diff(yy,a,b,idx));good=r['selected_n']>=15 and r['baseline_n']>=60 and (b&~a).sum()>=15;r['inference_eligible']=bool(good)
        if not good:r['p']=None
       else:r={'selected_n':int(a.sum()),'baseline_n':int(b.sum()),'inference_eligible':False,'p':None}
       results.append({'variant':variant,'period':period,'horizon':h,'target':target,'kind':'combo_contrast','name':name+'_vs_'+part,**r})
     for metric in METRICS:
      xx=frame[metric].to_numpy(dtype=float)[indices];v=valid&np.isfinite(xx);n=int(v.sum());r={'selected_n':n,'baseline_n':n,'inference_eligible':False,'p':None}
      if n>=15 and np.std(xx[v])>0 and np.std(yy[v])>0:
       xr=np.full(len(xx),np.nan);yr=xr.copy();xr[v]=rankdata(xx[v]);yr[v]=rankdata(yy[v]);xr[v]=(xr[v]-xr[v].mean())/xr[v].std();yr[v]=(yr[v]-yr[v].mean())/yr[v].std();rho=float(np.mean(xr[v]*yr[v]));score=np.where(v,xr*(yr-rho*xr)/n,0);score=np.nan_to_num(score);var=float(score@score)
       for lag in range(1,min(10,len(score)-1)+1):var+=2*(1-lag/11)*float(score[lag:]@score[:-lag])
       se=math.sqrt(max(var,0));r.update(rho=rho,p=math.erfc(abs(rho/se)/math.sqrt(2)) if se>0 and n>=60 else None,inference_eligible=n>=60)
       # Bootstrap rank-standardized correlation; no look-ahead fitting.
       cnt=v[idx].sum(1);sx=np.nan_to_num(xr)[idx];sy=np.nan_to_num(yr)[idx]
       with np.errstate(invalid='ignore',divide='ignore'):
        mx=sx.sum(1)/cnt;my=sy.sum(1)/cnt;cov=(sx*sy).sum(1)/cnt-mx*my;den=np.sqrt((sx*sx).sum(1)/cnt-mx*mx)*np.sqrt((sy*sy).sum(1)/cnt-my*my);boot=cov/den
       good=np.isfinite(boot)
       if good.sum()>=1800:r.update(ci_low=float(np.quantile(boot[good],.025)),ci_high=float(np.quantile(boot[good],.975)))
      results.append({'variant':variant,'period':period,'horizon':h,'target':target,'kind':'correlation','name':metric,**r})
  print('Statistics completed: '+variant,flush=True)
 save('thresholds_train_only.json',all_thresholds)
 hold=[i for i,r in enumerate(results) if r['period']=='2026'];primary=[i for i in hold if results[i]['variant']==VARIANTS[0] and results[i]['target'] in TARGETS[:2]]
 assert len(hold)==864 and len(primary)==144
 for family,ids in [('global',hold),('primary',primary)]:
  adj=holm([results[i].get('p') if results[i].get('p') is not None else 1 for i in ids],len(ids))
  for i,p in zip(ids,adj):results[i]['holm_'+family]=float(p)
 save('results.json',results);pd.DataFrame(results).to_csv(OUT/'results.csv',index=False)
 for h,(targets,ends) in labels.items():
  f=pd.DataFrame(targets,index=days);f['label_end']=ends;f.to_csv(OUT/f'future_labels_h{h}.csv',index_label='signal_date')
 return results

if __name__=='__main__':
 OUT.mkdir(exist_ok=True,parents=True)
 days,frames,outcomes=features();study(days,frames,outcomes)
 save('run_manifest.json',{'protocol_sha256':sha(OUT/'protocol.json'),'code_sha256':sha(__file__),'results_sha256':sha(OUT/'results.json'),'return_claims':False,'frozen_candidate_unchanged':True})
