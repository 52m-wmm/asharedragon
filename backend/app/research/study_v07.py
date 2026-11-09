"""T+1 09:25 study: proof of binary outcome != proof of exact final height."""
from collections import Counter
from datetime import date,datetime,time,timezone
from zoneinfo import ZoneInfo
import numpy as np
from scipy.stats import mannwhitneyu,false_discovery_control
from app.research.feature_study import FEATURES as OLD_FEATURES,finite,distribution,cliffs,cluster_ci,rate_summary,filter_rows
VERSION='dragon-research-v0.7'
TZ=ZoneInfo('Asia/Shanghai')
FEATURES={**OLD_FEATURES,**{k:v for k,v in [('open','首板开盘价'),('high','首板最高价'),('low','首板最低价'),('close','首板收盘价'),('volume','首板成交量'),('provider_limit_up_count','来源股票池涨停数（非全市场）')]}}
TEST_FAMILY=['circulating_market_cap','total_market_cap','turnover_rate','first_seal_seconds','open_times','seal_amount','amount','provider_limit_up_count']+[f'{p}_{n}' for p in ('raw_price_change','volume_change') for n in (5,10,20)]


def cycle_outcome(sample):
    if not sample.get('first_board_date'):return 'unknown','first_board_boundary_unknown'
    if sample.get('observed_min_height',0)>=5:return 'success','five_confirmed_consecutive_sessions'
    height=sample.get('exact_max_streak')
    if sample.get('complete_cycle_eligible') and height is not None and 1<=height<5:return 'failure','confirmed_cycle_ended_below_five'
    return 'unknown','future_cycle_outcome_unresolved'


def decision_cutoff(day,calendar):
    nxt=next((d for d in calendar if d>day),None)
    return datetime.combine(nxt,time(9,25),TZ).astimezone(timezone.utc) if nxt else None


def aware(value):
    if value is None:return None
    d=datetime.fromisoformat(value) if isinstance(value,str) else value
    if d.tzinfo is None:raise ValueError('Availability timestamp must have timezone')
    return d.astimezone(timezone.utc)


def availability(value,*,event_at,cutoff,fetched_at=None,first_available_at=None,publication_verified=False,evidence_id=None):
    event_at,cutoff,fetched_at,first_available_at=map(aware,(event_at,cutoff,fetched_at,first_available_at))
    # A later download is not evidence of historical unavailability.
    bounds=[]
    if evidence_id and fetched_at and event_at and fetched_at>=event_at:bounds.append(fetched_at)
    if publication_verified and evidence_id and first_available_at and event_at and first_available_at>=event_at:bounds.append(first_available_at)
    upper=min(bounds) if bounds else None
    ok=finite(value) and cutoff is not None and event_at is not None and event_at<cutoff and upper is not None and upper<cutoff
    reason='verified_before_cutoff' if ok else 'missing_value' if not finite(value) else 'missing_next_session' if cutoff is None else 'event_after_cutoff' if event_at and event_at>=cutoff else 'historical_availability_unverified'
    return dict(strict_value=value if ok else None,exploratory_value=value if finite(value) else None,
        historical_event_at_utc=event_at.isoformat() if event_at else None,collected_at_utc=fetched_at.isoformat() if fetched_at else None,
        first_available_at_utc=first_available_at.isoformat() if publication_verified and first_available_at else None,
        availability_upper_bound_utc=upper.isoformat() if upper else None,strict_eligible=bool(ok),availability_status=reason,evidence_id=evidence_id)


def study_statistics(all_rows,mode='exploratory',reps=1000,**filters):
    selected=filter_rows(all_rows,**filters);resolved=[r for r in selected if r['category']!='unknown']
    rows=[{**r,'features':r[mode+'_features']} for r in resolved]
    earliest={}
    for r in sorted(rows,key=lambda r:(r['first_board_date'],r['cycle_id'])):earliest.setdefault(r['security_id'],r)
    independent=list(earliest.values());comparisons=[];tested=[];pvals=[]
    for key,label in FEATURES.items():
        arrays=[[r['features'].get(key) for r in rows if r['outcome']==g and finite(r['features'].get(key))] for g in (1,0)]
        a,b=map(distribution,arrays)
        c=dict(feature=key,label=label,success=a,failure=b,missing=len(rows)-a['n']-b['n'],missing_fraction=1-(a['n']+b['n'])/len(rows) if rows else None,
            cliffs_delta=cliffs(*arrays),median_difference=a['median']-b['median'] if a['n'] and b['n'] else None,
            stock_bootstrap=cluster_ci(rows,key,reps=reps),date_bootstrap=cluster_ci(rows,key,'first_board_date',reps),one_cycle_p=None,bh_q=None)
        xs=[[r['features'].get(key) for r in independent if r['outcome']==g and finite(r['features'].get(key))] for g in (1,0)]
        c['one_cycle_effect']=cliffs(*xs);c['one_cycle_counts']=[len(x) for x in xs]
        if key in TEST_FAMILY and all(len(x)>=5 for x in xs):
            c['one_cycle_p']=float(mannwhitneyu(*xs,alternative='two-sided',method='asymptotic').pvalue);tested.append(c);pvals.append(c['one_cycle_p'])
        comparisons.append(c)
    if pvals:
        for c,q in zip(tested,false_discovery_control(pvals,method='bh')):c['bh_q']=float(q)
    def group(key,values):
        result=[]
        for value in values:
            subset=[r for r in selected if r[key]==value];known=[r for r in rows if r[key]==value]
            effects=[]
            for f in TEST_FAMILY:
                xs=[[r['features'].get(f) for r in known if r['outcome']==g and finite(r['features'].get(f))] for g in (1,0)]
                effects.append(dict(feature=f,success_n=len(xs[0]),failure_n=len(xs[1]),cliffs_delta=cliffs(*xs)))
            result.append(dict(group=value,unknown=sum(r['category']=='unknown' for r in subset),total_candidates=len(subset),
                **rate_summary(known,reps),effects=effects,formal_population_eligible=False))
        return result
    environment=[]
    for lo,hi in [(0,50),(50,100),(100,150),(150,None)]:
        rr=[r for r in rows if finite(r['features'].get('provider_limit_up_count')) and lo<=r['features']['provider_limit_up_count'] and (hi is None or r['features']['provider_limit_up_count']<hi)]
        environment.append(dict(lower=lo,upper=hi,**rate_summary(rr,reps)))
    return dict(mode=mode,analysis_level='exploratory' if mode=='exploratory' else 'strict_availability_audit',formal_population_eligible=False,
        cohort=rate_summary(rows,reps),one_cycle_sensitivity=rate_summary(independent,reps),unknown=sum(r['category']=='unknown' for r in selected),
        feature_eligible_samples=sum(any(finite(v) for v in r['features'].values()) for r in rows),
        strict_candidate_eligible_samples=sum(r.get('strict_candidate_eligible',False) for r in resolved),
        comparisons=comparisons,years=group('year',range(2017,2027)),months=group('month',range(1,13)),
        regimes=group('regime',sorted({r['regime'] for r in selected})),boards=group('board',sorted({r['board'] for r in selected})),
        market_environment=environment,test_family=TEST_FAMILY,bh_tests=len(tested),seed=600071,bootstrap_replicates=reps,
        coverage_denominator='Local evidence candidates only; unknown first-board date attributed to first observed year for audit, not seasonality')
