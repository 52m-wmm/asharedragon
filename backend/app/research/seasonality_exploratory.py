"""Local exploratory seasonality; independent cohorts never pooled."""
from collections import Counter
from app.research.feature_study import rate_summary,distribution,cliffs,finite
VERSION='dragon-seasonality-exploratory-v1'


def summarize(rows):
    started=[r for r in rows if r['first_board_date']]
    s=sum(r['outcome']=='success' for r in started);f=sum(r['outcome']=='failure' for r in started)
    u=sum(r['outcome']=='unknown' for r in started);unplaced=sum(not r['first_board_date'] for r in rows)
    known=[{**r,'outcome':int(r['outcome']=='success')} for r in started if r['outcome']!='unknown']
    n=s+f;total=n+u
    return dict(candidates=len(rows),dated_first_boards=len(started),success=s,failure=f,unknown=u,
        undated=unplaced,effective_denominator=n,dated_denominator=total,known_outcome_fraction=n/total if total else None,
        promotion_rate=s/n if n else None,
        missing_outcome_bounds=[s/total,(s+u)/total] if total else None,
        observed_ge={str(k):sum(r['observed_height']>=k for r in started) for k in (3,5,7,10)},
        exact_ge={str(k):sum(r['exact_height'] is not None and r['exact_height']>=k for r in started) for k in (3,5,7,10)},
        rate_uncertainty=rate_summary(known,reps=1000),formal_population_rate=None)


def statistics(rows,cohort='direct',grade='all',start_year=2017,end_year=2026,board=None,view='exploratory'):
    if start_year>end_year:raise ValueError('开始年份不能晚于结束年份')
    selected=[r for r in rows if r['cohort']==cohort and start_year<=r['audit_year']<=end_year and (grade=='all' or r['evidence_grade']==grade) and (board is None or r['board']==board)]
    if view=='formal':return dict(months=[],years=[],summary=None,formal_eligible=False,reason='No complete representative year accepted; local verified facts are not formal annual statistics')
    monthly=[]
    for month in range(1,13):
        dated=[r for r in selected if r['first_board_date'] and r['start_month']==month]
        undated=[r for r in selected if not r['first_board_date'] and r['observation_month']==month]
        x=summarize(dated);d=x['dated_denominator'];s=x['success'];u=x['unknown'];missing=len(undated)
        # Scenario only: unlocated starts assigned to first observation month, never called real starts.
        x.update(month=month,undated_observed_in_month=missing,
            observation_month_scenario_bounds=[s/(d+missing),(s+u+missing)/(d+missing)] if d+missing else None,
            all_undated_worst_case_bounds=[s/(d+sum(not r['first_board_date'] for r in selected)),(s+u+sum(not r['first_board_date'] for r in selected))/(d+sum(not r['first_board_date'] for r in selected))] if d+sum(not r['first_board_date'] for r in selected) else None)
        monthly.append(x)
    years=[dict(year=y,**summarize([r for r in selected if r['audit_year']==y])) for y in range(start_year,end_year+1)]
    feature_stats=[]
    for k in ('first_seal_seconds','seal_amount','circulating_market_cap','turnover_rate','raw_price_change_5','raw_price_change_10','raw_price_change_20','volume_change_5'):
        known=[r for r in selected if r['outcome']!='unknown' and r['first_board_date']]
        arrays=[[r.get('features',{}).get(k) for r in known if r['outcome']==group and finite(r.get('features',{}).get(k))] for group in ('success','failure')]
        feature_stats.append(dict(feature=k,success=distribution(arrays[0]),failure=distribution(arrays[1]),cliffs_delta=cliffs(*arrays),missing_fraction=1-sum(map(len,arrays))/len(known) if known else None,strict_backtest_eligible=False))
    return dict(features=feature_stats,summary=summarize(selected),months=monthly,years=years,formal_eligible=False,cohort=cohort,grade=grade,
        grade_counts=dict(Counter(r['evidence_grade'] for r in selected)),
        warning='Cohorts are alternative evidence populations, not additive. Bounds address unknown outcomes, not unseen market candidates or nonrandom selection.')
