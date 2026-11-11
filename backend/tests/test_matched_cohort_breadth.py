from copy import deepcopy
from itertools import product
from fractions import Fraction

import pytest

from app.analytics.matched_cohort_breadth import diagnose, matched_breadth, old_primary


def stock(value,reason='valid'):
    return dict(price_reason=reason,price_source='vendor',reference_basis='same_response_reference_pct_checked',
        daily_return=value if reason=='valid' else None,price_conflict=False,identity_status='unknown',
        trade_status=False if reason=='suspended' else True,close=10,reference_price=10)


def profile(day,values):
    return dict(trade_date=day,source_snapshot='u',breadth_rule_version='e1',scope='observed',
                stocks={c:stock(v) for c,v in values.items()})


def pair():
    return (profile('2026-11-06',dict(a=.01,b=.01,c=-.01,d=-.01)),
            profile('2026-11-09',dict(a=.01,b=.01,c=.01,d=-.01)))


def run(a,b):
    return matched_breadth('2026-11-09','2026-11-06',a,b,snapshot_id='d1',input_snapshots={'old':'v1'})


def test_same_cohort_uses_true_previous_session_and_exact_counts():
    a,b=pair();r=run(a,b)
    assert (r['previous']['up'],r['current']['up'],r['common_count'],r['delta'])==(2,3,4,25)
    assert r['status']=='comparable' and r['direction']=='positive'
    assert r['coverage_ratio'] is None and r['current']['identity_counts']=={'unknown':4}
    assert r['composition']['composition_effect_pp']==0
    assert r==run(deepcopy(a),deepcopy(b))


@pytest.mark.parametrize('change',['new_stock','suspension','reference_quality'])
def test_one_stock_change_keeps_interpretable_common_cohort(change):
    a,b=pair()
    if change=='new_stock': b['stocks']['e']=stock(-.02)
    if change=='suspension': b['stocks']['d']=stock(None,'suspended')
    if change=='reference_quality': b['stocks']['d']=stock(None,'reference_unverified')
    r=run(a,b);audit=diagnose(a,b)
    assert r['calculable'] and r['cohort_direction']=='positive' and r['delta']>0
    assert r['common_count']==(4 if change=='new_stock' else 3)
    assert audit['current']['observed_count']==len(b['stocks'])
    category={'new_stock':'observation_membership_unexplained','suspension':'observed_suspension_evidence','reference_quality':'price_quality_issue'}[change]
    assert audit['category_counts'][category]==1
    assert 'new_listing_confirmed' not in str(audit)


@pytest.mark.parametrize('change',['source','reference','conflict','untrusted_reference','nan'])
def test_untrusted_common_stock_is_not_silently_removed(change):
    a,b=pair();s=b['stocks']['a']
    if change=='source':s['price_source']='other'
    if change=='reference':s['reference_basis']='raw_close_change_pct_ohlc_checked'
    if change=='conflict':s['price_conflict']=True
    if change=='untrusted_reference':s['reference_basis']='unverified'
    if change=='nan':s['daily_return']=float('nan')
    r=run(a,b)
    assert r['common_count']==4 and not r['calculable'] and r['delta'] is None and r['direction']=='unknown'


@pytest.mark.parametrize('field',['source_snapshot','breadth_rule_version','scope','trade_date'])
def test_metadata_incompatibility_rejects_comparison(field):
    a,b=pair();a[field]='changed'
    assert not run(a,b)['calculable']


@pytest.mark.parametrize('case',['missing_previous','zero_previous','zero_current','disjoint'])
def test_missing_or_empty_evidence_never_becomes_zero(case):
    a,b=pair()
    if case=='missing_previous':a=None
    if case=='zero_previous':a['stocks']={}
    if case=='zero_current':b['stocks']={}
    if case=='disjoint':b['stocks']={'z':stock(.01)}
    r=run(a,b)
    assert not r['calculable'] and r['value'] is None and r['delta'] is None


def test_same_ratio_with_composition_change_is_disclosed():
    a=profile('2026-11-06',dict(a=.1,b=-.1,c=.1))
    b=profile('2026-11-09',dict(a=.1,b=-.1,d=-.1))
    r=run(a,b)
    assert r['calculable'] and r['delta']==0 and r['cohort_direction']=='neutral'
    assert r['status']=='composition_sensitive' and r['direction']=='unknown'
    assert r['composition']['full_delta_pp']==pytest.approx(-100/3)
    assert r['composition']['composition_effect_pp']==pytest.approx(-100/3)


def test_opposite_directions_remain_mixed_not_single_bullish():
    a=profile('2026-11-06',dict(a=.1,b=-.1,c=.1,d=.1,e=.1,f=.1))
    b=profile('2026-11-09',dict(a=.1,b=.1,g=-.1,h=-.1,i=-.1,j=-.1))
    r=run(a,b)
    assert r['calculable'] and r['cohort_direction']=='positive' and r['status']=='mixed'
    assert r['direction']=='unknown' and r['composition']['full_delta_pp']<0
    assert 'unpaired_count_ge_common_count' in r['reason_codes']


def test_same_full_ratio_does_not_mean_same_membership_or_cohort_ratio():
    a=profile('2026-11-06',dict(a=.1,b=-.1,c=.1,d=-.1))
    b=profile('2026-11-09',dict(a=.1,b=.1,e=-.1,f=-.1))
    r=run(a,b)
    assert r['composition']['full_delta_pp']==0 and r['delta']==50
    assert r['status']=='composition_sensitive'


def test_union_sensitivity_bounds_match_exhaustive_binary_completions():
    a=profile('2026-11-06',dict(a=.1,b=-.1,c=.1))
    b=profile('2026-11-09',dict(a=.1,b=.1,d=-.1,e=.1))
    r=run(a,b);observed=[]
    # Unknown prior outcomes for d/e; unknown current outcome for c.
    for prior_d,prior_e,current_c in product((0,1),repeat=3):
        observed.append(float(Fraction((3+current_c)-(2+prior_d+prior_e),5)*100))
    assert r['composition']['union_completion_delta_bounds_pp']==[min(observed),max(observed)]
    assert r['composition']['full_delta_pp']==pytest.approx(r['delta']+r['composition']['composition_effect_pp'])
    assert r['composition']['composition_effect_pp']==pytest.approx(
        r['composition']['current_composition_gap_pp']-r['composition']['previous_composition_gap_pp'])


def test_transition_matrix_conserves_union_and_lists_unresolved_absence():
    a,b=pair();b['stocks'].pop('d');b['stocks']['e']=stock(None,'reference_missing')
    audit=diagnose(a,b)
    assert sum(r['count'] for r in audit['quality_transitions'])==5
    assert audit['observed_added_codes']==['e'] and audit['observed_removed_codes']==['d']
    assert audit['valid_removed_codes']==['d']
    assert all('observation_membership_unexplained' in r['categories'] for r in audit['stock_changes'])


def test_old_primary_is_exclusive_and_does_not_add_overlaps():
    assert old_primary(['value_missing_or_nonfinite','valid_sample_changed_without_verified_explanation'])=='missing_usable_comparison'
    assert old_primary(['price_source_or_reference_basis_changed','per_stock_price_quality_changed'])=='source_or_reference_change'
    assert old_primary(['per_stock_price_quality_changed'])=='sample_or_quality_change'
    assert old_primary([])=='comparable'


def test_unrelated_labels_cannot_change_cohort_and_no_strict_promotion():
    a,b=pair();expected=run(a,b)
    for row in b['stocks'].values(): row.update(delisting_date='2099-01-01',final_height=100,lifecycle_conflict=True)
    assert run(a,b)==expected and not expected['eligible_for_formal_counts']
