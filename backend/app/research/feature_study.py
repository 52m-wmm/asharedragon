"""Frozen exploratory first-board study. Outcomes and feature namespaces are separate."""
from collections import Counter,defaultdict
from datetime import date,datetime,time,timezone
from zoneinfo import ZoneInfo
import hashlib
import math
import numpy as np
from scipy.stats import mannwhitneyu,false_discovery_control
from app.research.dragons import wilson

VERSION='dragon-feature-v0.6'
SEED=600071
BOOTSTRAPS=1000
TZ=ZoneInfo('Asia/Shanghai')
REQUESTED={
 'circulating_market_cap':'流通市值','total_market_cap':'总市值','pe':'PE','pb':'PB',
 'turnover_rate':'首板换手率','amount':'首板成交额','first_seal_seconds':'首次封板时刻',
 'open_times':'炸板次数','seal_amount':'封单金额',
 'pre5_return':'前5日收益','pre10_return':'前10日收益','pre20_return':'前20日收益',
 'volume_change_5':'前5日成交量变化','volume_change_10':'前10日成交量变化','volume_change_20':'前20日成交量变化',
 'market_limit_up_count':'当日全市场涨停数','market_max_height':'当日市场最高连板'}
EXTRA={f'raw_price_change_{n}':f'前{n}日原价变化（非总收益）' for n in (5,10,20)}
FEATURES={**REQUESTED,**EXTRA}
TEST_FAMILY=list(EXTRA)+[f'volume_change_{n}' for n in (5,10,20)]


def finite(v):
    try:return v is not None and math.isfinite(float(v))
    except (ValueError,TypeError):return False


def cutoff(day):
    return datetime.combine(day,time(15),TZ).astimezone(timezone.utc)


def features_before_close(day,calendar,bars):
    """Only completed earlier sessions. Same-day final aggregates are never inputs.
    Historical download time is NOT claimed to be a contemporaneous feed timestamp.
    Lagged raw observations use event-time ordering; certified vendor PIT stays false.
    """
    values={k:None for k in FEATURES}
    reasons={k:'same_day_eod_or_publication_time_unverified' for k in FEATURES}
    for k in ('pe','pb'):reasons[k]='no_verified_historical_preclose_valuation'
    for k in ('market_limit_up_count','market_max_height'):reasons[k]='eod_value_and_full_market_coverage_unverified'
    for n in (5,10,20):reasons[f'pre{n}_return']='corporate_action_and_total_return_basis_uncertified'
    prior=[d for d in calendar if d<day];provenance={}
    for n in (5,10,20):
        ds=prior[-2*n:];rows=[bars.get(d) for d in ds]
        vol_ok=len(ds)==2*n and all(r and finite(r.get('volume')) and r['volume']>0 and r.get('adjustment_type')=='none' for r in rows)
        k=f'volume_change_{n}'
        if vol_ok:
            values[k]=sum(r['volume'] for r in rows[-n:])/sum(r['volume'] for r in rows[:n])-1
            reasons[k]=None
        else:reasons[k]='incomplete_or_invalid_prior_volume_window'
        prices=prior[-(n+1):];rs=[bars.get(d) for d in prices];k2=f'raw_price_change_{n}'
        complete=len(prices)==n+1 and all(r and finite(r.get('close')) and r['close']>0 and r.get('adjustment_type')=='none' for r in rs)
        bridge=complete and all(finite(r.get('pre_close')) and abs(r['pre_close']-p['close'])<=.011 for p,r in zip(rs,rs[1:]))
        if bridge:values[k2]=rs[-1]['close']/rs[0]['close']-1;reasons[k2]=None
        else:reasons[k2]='missing_window_or_observed_reference_break'
        for key,dates in ((k,ds),(k2,prices)):
            provenance[key]=dict(window_dates=dates,window_end=dates[-1] if dates else None,
                market_event_end_utc=cutoff(dates[-1]).isoformat() if dates else None,
                availability_basis='prior_completed_market_sessions_not_vendor_publication_timestamp',
                sources=sorted({bars[d]['source'] for d in dates if d in bars}),
                vendor_pit_verified=False,corporate_actions_certified=False)
    return values,reasons,provenance


def board_context(code,day):
    if code.startswith('68'):return 'star','star_20_or_special_unknown'
    if code.startswith('30'):
        return 'chinext','chinext_pre_reform_10_or_5_special_unknown' if day<date(2020,8,24) else 'chinext_post_reform_20_or_special_unknown'
    return 'main','main_historical_10_or_5_special_unknown'


def distribution(x):
    a=np.asarray(x,dtype=float)
    if not len(a):return dict(n=0,min=None,q1=None,median=None,q3=None,max=None)
    q=np.quantile(a,[0,.25,.5,.75,1]);return dict(n=len(a),**dict(zip(('min','q1','median','q3','max'),map(float,q))))


def cliffs(x,y):
    x=np.asarray(x,dtype=float);y=np.sort(np.asarray(y,dtype=float))
    if not len(x) or not len(y):return None
    return float(np.sum(np.searchsorted(y,x,'left')-(len(y)-np.searchsorted(y,x,'right')))/(len(x)*len(y)))


def cluster_ci(rows,key,cluster='security_id',reps=BOOTSTRAPS):
    valid=[r for r in rows if finite(r['features'].get(key))]
    groups=[[r for r in valid if r['outcome']==v] for v in (1,0)]
    if any(len({r[cluster] for r in group})<5 for group in groups):return dict(delta_ci=None,median_difference_ci=None,valid_replicates=0,reason='fewer_than_5_clusters_per_group')
    names=sorted({r[cluster] for r in valid});index={v:i for i,v in enumerate(names)}
    xy=[];ix=[]
    for group in groups:
        group=sorted(group,key=lambda r:r['features'][key]);xy.append(np.array([r['features'][key] for r in group],float));ix.append(np.array([index[r[cluster]] for r in group]))
    x,y=xy;lo=np.searchsorted(y,x,'left');hi=np.searchsorted(y,x,'right')
    rng=np.random.default_rng(SEED+int(hashlib.sha256((key+cluster).encode()).hexdigest()[:8],16));deltas=[];medians=[]
    for _ in range(reps):
        w=np.bincount(rng.integers(0,len(names),len(names)),minlength=len(names));wx,wy=w[ix[0]],w[ix[1]];nx,ny=wx.sum(),wy.sum()
        if not nx or not ny:continue
        c=np.r_[0,np.cumsum(wy)];deltas.append(float(np.sum(wx*(c[lo]-(ny-c[hi])))/(nx*ny)))
        medians.append(float(np.median(np.repeat(x,wx))-np.median(np.repeat(y,wy))))
    good=len(deltas)>=.8*reps
    return dict(delta_ci=list(map(float,np.quantile(deltas,[.025,.975]))) if good else None,
        median_difference_ci=list(map(float,np.quantile(medians,[.025,.975]))) if good else None,
        valid_replicates=len(deltas),reason=None if good else 'insufficient_valid_bootstrap_replicates')


def rate_summary(rows,reps=BOOTSTRAPS):
    n=len(rows);success=sum(r['outcome'] for r in rows)
    def ci(cluster):
        counts=defaultdict(lambda:[0,0])
        for r in rows:counts[r[cluster]][0]+=r['outcome'];counts[r[cluster]][1]+=1
        if len(counts)<5 or sum(v[0]>0 for v in counts.values())<5 or sum(v[1]>v[0] for v in counts.values())<5:return None
        a=np.array([counts[k] for k in sorted(counts)]);rng=np.random.default_rng(SEED);rates=[]
        for _ in range(reps):
            b=a[rng.integers(0,len(a),len(a))].sum(axis=0);rates.append(float(b[0]/b[1]))
        return list(map(float,np.quantile(rates,[.025,.975])))
    return dict(n=n,success=success,failure=n-success,rate=success/n if n else None,
        ci_stock=ci('security_id') if n else None,ci_date=ci('first_board_date') if n else None,
        wilson_iid_reference=wilson(success,n),stock_clusters=len({r['security_id'] for r in rows}),
        date_clusters=len({r['first_board_date'] for r in rows}),formal_population_probability=None,ci_policy='at least five clusters with each outcome; no zero-width all-success/failure bootstrap interval')


def filter_rows(rows,start_year=2017,end_year=2026,month=None,board=None,regime=None):
    if start_year>end_year:raise ValueError('开始年份不能晚于结束年份')
    return [r for r in rows if start_year<=r['year']<=end_year and (month is None or r['month']==month)
            and (board is None or r['board']==board) and (regime is None or r['regime']==regime)]


def analyze(all_rows,*,reps=BOOTSTRAPS,**filters):
    eligible=filter_rows(all_rows,**filters);rows=[r for r in eligible if r['primary_included']]
    primary=rate_summary(rows,reps);closed=rate_summary(eligible,reps)
    first={}
    for r in sorted(rows,key=lambda r:(r['first_board_date'],r['cycle_id'])):first.setdefault(r['security_id'],r)
    independent=list(first.values());comparisons=[];pvals=[];tested=[]
    for key,label in FEATURES.items():
        arrays=[[r['features'][key] for r in rows if r['outcome']==v and finite(r['features'].get(key))] for v in (1,0)]
        a,b=map(distribution,arrays);valid=a['n']+b['n']
        c=dict(feature=key,label=label,success=a,failure=b,missing=len(rows)-valid,missing_fraction=(len(rows)-valid)/len(rows) if rows else None,
            archived_observed=sum(finite(r.get('archived',{}).get(key)) for r in rows),
            null_reasons=dict(Counter(r['null_reasons'].get(key,'missing') for r in rows if not finite(r['features'].get(key)))),
            median_difference=a['median']-b['median'] if a['n'] and b['n'] else None,cliffs_delta=cliffs(*arrays),
            stock_bootstrap=cluster_ci(rows,key,reps=reps),date_bootstrap=cluster_ci(rows,key,'first_board_date',reps),
            one_cycle_per_security_p=None,bh_q=None,analysis_level='exploratory',is_total_return=False if key in EXTRA else None)
        ix=[[r['features'][key] for r in independent if r['outcome']==v and finite(r['features'].get(key))] for v in (1,0)]
        c['one_cycle_counts']=[len(x) for x in ix]
        if key in TEST_FAMILY and all(len(x)>=5 for x in ix):
            c['one_cycle_per_security_p']=float(mannwhitneyu(*ix,alternative='two-sided',method='asymptotic').pvalue)
            pvals.append(c['one_cycle_per_security_p']);tested.append(c)
        # Predeclared bins; never tune cut-points to outcomes.
        edges=[-np.inf,-.1,0,.1,.2,np.inf] if key in EXTRA else [-np.inf,-.5,0,.5,1,np.inf] if key.startswith('volume_change') else []
        c['bins']=[]
        for left,right in zip(edges,edges[1:]):
            group=[r for r in rows if finite(r['features'].get(key)) and left<=r['features'][key]<right]
            c['bins'].append(dict(lower=None if np.isinf(left) else left,upper=None if np.isinf(right) else right,**rate_summary(group,reps)))
        comparisons.append(c)
    if pvals:
        for c,q in zip(tested,false_discovery_control(pvals,method='bh')):c['bh_q']=float(q)
    def group_stats(key,values):
        return [dict(group=value,**rate_summary([r for r in rows if r[key]==value],reps),
            parent_complete_cycles=sum(r[key]==value for r in eligible)) for value in values]
    # No tests for group comparisons: used to reveal composition and confounding.
    strata=[]
    for board in sorted({r['board'] for r in rows}):
        rr=[r for r in rows if r['board']==board and r['year']==2020]
        strata.append(dict(board=board,year=2020,**rate_summary(rr,reps),
            effects={k:cliffs([r['features'][k] for r in rr if r['outcome']==1 and finite(r['features'][k])],[r['features'][k] for r in rr if r['outcome']==0 and finite(r['features'][k])]) for k in TEST_FAMILY}))
    return dict(cohort=primary,closed_cycle_sensitivity=closed,one_cycle_sensitivity=rate_summary(independent,reps),
        comparisons=comparisons,seasonality=group_stats('month',range(1,13)),years=group_stats('year',range(2017,2027)),
        boards=group_stats('board',sorted({r['board'] for r in eligible})),regimes=group_stats('regime',sorted({r['regime'] for r in eligible})),
        composition_sensitivity=strata,market_environment=[],market_environment_missing=len(rows),
        tested_family=TEST_FAMILY,bh_test_count=len(tested),bootstrap_replicates=reps,seed=SEED,
        analysis_level='exploratory',labels_level='locally_verified',formal_population_eligible=False,
        temporal_policy='strictly earlier completed sessions only; same-day final and unverified publication fields NULL; vendor PIT not certified')
