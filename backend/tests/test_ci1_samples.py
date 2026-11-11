"""Pure decision tests; no network, database writes, or real snapshot mutation."""
from decimal import Decimal
import pytest
from scripts.audit_ci1_samples import reconcile, equivalent


def candidate(source,value,valid=True,available=True,identity=None):
    return dict(source=source,value=value,valid=valid,availability='available' if available else 'not_available',observation_id=identity or source)


def test_agreement_requires_two_provider_families():
    a=candidate('akshare',Decimal('10.10'))
    copy=candidate('akshare',10.1,identity='archive_copy')
    assert reconcile([a,copy],'close')['status']=='single_source_validated'
    result=reconcile([a,candidate('baostock','10.1000')],'close')
    assert result['status']=='corroborated' and result['selected_source'] is None


def test_absence_is_not_conflict_or_failed_validation():
    missing=candidate('tushare_proxy',None,False,False)
    assert reconcile([candidate('purchased',10),missing],'pre_close')['status']=='single_source_validated'
    assert reconcile([missing],'official_limit_up_price')['status']=='unknown'


def test_majority_cannot_choose_winner_and_preserves_values():
    values=[candidate('akshare',10),candidate('baostock',10),candidate('purchased',11)]
    r=reconcile(values,'close')
    assert r['status']=='conflict' and r['consensus_value'] is None
    assert r['selected_value'] is None and len(r['conflict_pairs'])==2
    assert values[-1]['value']==11


def test_same_source_revision_disagreement_is_not_independent_confirmation():
    assert reconcile([candidate('akshare',10),candidate('akshare',11,identity='revision')],'close')['status']=='conflict'


def test_present_quality_failure_blocks_promotion_even_if_other_source_valid():
    r=reconcile([candidate('purchased',10),candidate('baostock',10,False)],'pre_close')
    assert r['status']=='unknown' and r['reason']=='present_but_quality_or_provenance_unresolved'


def test_field_states_do_not_inherit_price_confirmation():
    assert reconcile([candidate('akshare',10),candidate('purchased',10)],'close')['status']=='corroborated'
    assert reconcile([candidate('baostock',False)],'is_st')['status']=='single_source_validated'
    assert reconcile([candidate('akshare',None,False,False)],'is_st')['status']=='unknown'
    assert reconcile([candidate('baostock',True),candidate('purchased',False)],'is_st')['status']=='conflict'


@pytest.mark.parametrize('a,b,expected',[(10,10.0000001,True),(10,10.01,False),(10,10.004,False)])
def test_tolerance_does_not_hide_tick_or_reference_difference(a,b,expected):
    assert equivalent(a,b,'pre_close') is expected
