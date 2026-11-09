from datetime import date,timedelta
import pytest
from app.research.feature_study import features_before_close,cliffs,analyze,cutoff,FEATURES,board_context,cluster_ci


def fixture():
    ds=[date(2020,1,1)+timedelta(days=i) for i in range(50)]
    bars={d:dict(close=10+i,pre_close=9+i,volume=100+i,source='test',adjustment_type='none') for i,d in enumerate(ds)}
    return ds,bars


def test_cutoff_and_no_same_day_leakage():
    ds,bars=fixture();d=ds[-1];values,_,provenance=features_before_close(d,ds,bars)
    bars[d].update(close=99999,volume=99999)
    assert features_before_close(d,ds,bars)[0]==values
    assert values['raw_price_change_5']==pytest.approx(58/53-1)
    assert values['circulating_market_cap'] is None and values['pre5_return'] is None
    assert provenance['raw_price_change_5']['window_end']<d
    assert cutoff(d).isoformat().endswith('07:00:00+00:00')


def test_gap_and_reference_break_not_zero_returns():
    ds,bars=fixture();bars.pop(ds[-3]);v,_,_=features_before_close(ds[-1],ds,bars)
    assert v['volume_change_5'] is None and v['raw_price_change_5'] is None
    ds,bars=fixture();bars[ds[-3]]['pre_close']=1
    v,_,_=features_before_close(ds[-1],ds,bars)
    assert v['raw_price_change_5'] is None and v['volume_change_5'] is not None


def test_effect_direction_ties_and_empty():
    assert cliffs([2,2],[1,2])==.5
    assert cliffs([1],[2])==-1
    assert cliffs([],[]) is None


def test_filters_repeated_security_and_closed_cycle_sensitivity():
    rows=[]
    for i in range(12):
        rows.append(dict(cycle_id=str(i),security_id=str(i//2),first_board_date=f'2020-01-{i+1:02}',year=2020,month=1,board='main',regime='test',outcome=i%2,primary_included=i!=11,
                         features={k:None for k in FEATURES},null_reasons={k:'unavailable' for k in FEATURES}))
    result=analyze(rows,reps=20)
    assert result['cohort']['n']==11 and result['closed_cycle_sensitivity']['n']==12
    assert result['one_cycle_sensitivity']['n']==6 and result['one_cycle_sensitivity']['success']==0
    assert analyze(rows,reps=20,start_year=2021)['cohort']['rate'] is None
    assert all(c['success']['n']==0 for c in result['comparisons'])
    with pytest.raises(ValueError):analyze(rows,start_year=2026,end_year=2017)


def test_cluster_bootstrap_reproducible_and_min_clusters():
    rows=[dict(security_id=str(i),first_board_date=str(i),outcome=int(i<6),features={'x':i}) for i in range(12)]
    assert cluster_ci(rows,'x',reps=30)==cluster_ci(rows,'x',reps=30)
    assert cluster_ci(rows[:7],'x',reps=30)['delta_ci'] is None


def test_regime_context_does_not_infer_st():
    assert board_context('300001.SZ',date(2020,8,23))[1].startswith('chinext_pre')
    assert board_context('300001.SZ',date(2020,8,24))[1].startswith('chinext_post')
