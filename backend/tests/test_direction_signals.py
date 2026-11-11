from copy import deepcopy
import json

import pytest

from app.analytics.direction_signals import breadth_comparison, daily_signals, signal, limit_comparison


def breadth(day, up=2):
    return dict(trade_date=day,status='observed_sample',observed_count=4,price_valid_count=4,
                price_unknown_count=0,up=up,down=4-up,flat=0,advancing_ratio=up/4,
                median_return=(up-2)/100,non_st_confirmed_count=0,st_confirmed_count=0,
                st_unknown_count=4,valid_non_st_count=0,valid_st_count=0,valid_st_unknown_count=4,
                coverage_ratio=None,universe_status='unknown',lifecycle_conflict_observed_count=2)


def profile():
    return dict(observed={c:'valid' for c in 'abcd'},valid={c:('vendor','checked') for c in 'abcd'},
                critical_price_conflicts=0)


def limits(day,prev,n=2,p=1):
    return dict(trade_date=day,previous_trading_day=prev,provider='akshare',rule_version='e2',
        snapshot_id='e2-snapshot',source_snapshot='unified',overlap_codes=[],
        pools={kind:dict(status='supplier_observed',scope_verified=False,scope='SSE_SZSE_A_shares',
                        provider='akshare',trade_date=day,raw_ids=[1]) for kind in ('up','broken')},
        strict_non_st=dict(up_count=None,broken_limit_rate=None,break_numerator=None,break_denominator=None),
        supplier_diagnostic=dict(limit_up_count=n,broken_limit_rate=1/(n+1),break_numerator=1,break_denominator=n+1),
        reasons=dict(up=['identity_unknown'],broken=['identity_unknown']),
        **{key:dict(status='unavailable',cohort_status='unavailable',cohort_reasons=['identity_unknown'],
                   unresolved_count=None,fixed_denominator=None,rate=None,counts=dict(identity_unknown=2),
                   supplier_diagnostic=dict(status='diagnostic',promoted_count=p,fixed_denominator=2,rate=p/2))
           for key in ('promotion_1to2','promotion_2to3')})


def run(b=None,old=None,l=None,old_l=None,comparison=None):
    return daily_signals('2026-11-09','2026-11-06', b or breadth('2026-11-09',3),
        old or breadth('2026-11-06'),l or limits('2026-11-09','2026-11-06',3,0),
        old_l or limits('2026-11-06','2026-11-05'),
        comparison or breadth_comparison(profile(),profile()),snapshot_id='e3',input_snapshots={'breadth':'e1','limits':'e2'})


def get(record,layer,metric):
    return next(s for s in record['signals'] if s['id']==layer+'.'+metric)


def test_required_contract_and_determinism():
    a=run();assert json.dumps(a,sort_keys=True)==json.dumps(run(),sort_keys=True)
    required={'id','name','direction','value','previous_value','delta','unit','reason','status','source','snapshot_id','rule_version'}
    assert len(a['signals'])==12
    assert all(required<=s.keys() for s in a['signals'])
    assert all(s['previous_trading_day']=='2026-11-06' for s in a['signals'])


def test_unknown_st_and_unrelated_lifecycle_do_not_block_price_breadth():
    s=get(run(),'observed_breadth','advancing_ratio')
    assert (s['status'],s['direction'],s['delta'])==('formal','positive',25)
    assert s['basis']['current']['st_unknown_count']==4
    assert s['basis']['current']['coverage_ratio'] is None
    assert s['basis']['current']['lifecycle_conflict_observed_count']==2


def test_diagnostics_are_separate_and_can_coexist():
    r=run()
    assert r['counts']['supplier_diagnostic']==dict(positive=2,negative=2,neutral=0,unknown=0)
    assert r['counts']['formal']==dict(positive=4,negative=0,neutral=0,unknown=4)
    for s in r['signals'][8:]:
        assert s['status']=='diagnostic' and not s['eligible_for_formal_counts']
        assert '供应商诊断' in s['reason'] and s['source']['provider']=='akshare'
    for s in r['signals'][4:8]:
        assert s['direction']=='unknown' and s['value'] is None and s['delta'] is None
    assert get(r,'strict_non_st','promotion_2to3')['basis']['queue_quality']['current']['counts']['identity_unknown']==2


def test_formal_positive_and_negative_coexist():
    b=breadth('2026-11-09',3);b['median_return']=-.01
    r=run(b=b)
    assert r['counts']['formal']['positive']==3 and r['counts']['formal']['negative']==1


@pytest.mark.parametrize('metric,unit,polarity,new,old,expected,delta',[
    ('up','count',1,3,2,'positive',1),('down','count',-1,3,2,'negative',1),
    ('ratio','ratio',1,.3,.4,'negative',-10),('broken','ratio',-1,.3,.4,'positive',-10),
    ('up','count',1,0,0,'neutral',0),('ratio','ratio',1,.4,.4,'neutral',0),
    ('up','count',1,None,2,'unknown',None),('ratio','ratio',1,float('nan'),.4,'unknown',None)])
def test_direction_arithmetic(metric,unit,polarity,new,old,expected,delta):
    s=signal(metric,metric,unit,polarity,'observed_breadth',new,old,[],day='d',previous_day='p',source={},snapshot_id='s',input_snapshots={},basis={})
    assert (s['direction'],s['delta'])==(expected,delta)


@pytest.mark.parametrize('change,reason',[
    ('membership','valid_sample_changed_without_verified_explanation'),
    ('source','price_source_or_reference_basis_changed'),
    ('reference','price_source_or_reference_basis_changed'),
    ('quality','per_stock_price_quality_changed'),('conflict','critical_price_conflict')])
def test_sample_gate_checks_codes_and_source_not_only_counts(change,reason):
    a=profile();b=deepcopy(a)
    if change=='membership': b['valid']['e']=b['valid'].pop('a');b['observed']['e']=b['observed'].pop('a')
    if change=='source': b['valid']['a']=('other','checked')
    if change=='reference': b['valid']['a']=('vendor','other_reference')
    if change=='quality': b['observed']['a']='reference_unverified'
    if change=='conflict': b['critical_price_conflicts']=1
    comparison=breadth_comparison(a,b)
    assert reason in comparison['reasons']
    if change in ('source','reference'): assert comparison['overlap']['jaccard']==1
    assert all(s['direction']=='unknown' and s['delta'] is None for s in run(comparison=comparison)['signals'][:4])


def test_missing_previous_day_never_falls_back():
    r=daily_signals('2026-11-09','2026-11-06',breadth('2026-11-09'),None,
        limits('2026-11-09','2026-11-06'),None,breadth_comparison(None,profile()),snapshot_id='e3',input_snapshots={})
    assert all(s['direction']=='unknown' and s['previous_value'] is None and s['delta'] is None for s in r['signals'])
    r=run(old=breadth('2026-11-05'),old_l=limits('2026-11-05','2026-11-04'))
    assert all(s['direction']=='unknown' for s in r['signals'])


@pytest.mark.parametrize('field', ['provider','rule_version','snapshot_id','source_snapshot'])
def test_supplier_snapshot_version_changes_block_diagnostic(field):
    old=limits('2026-11-06','2026-11-05');old[field]='changed'
    assert all(s['direction']=='unknown' for s in run(old_l=old)['signals'][4:])
    old[field]=None
    assert any('missing' in r for r in limit_comparison(old,limits('2026-11-09','2026-11-06'),'limit_up_count'))


@pytest.mark.parametrize('mutation',['zero','missing','bad_ratio','overlap','pool_provider','pool_date','scope'])
def test_bad_supplier_evidence_never_produces_conclusion(mutation):
    l=limits('2026-11-09','2026-11-06',3,0)
    if mutation=='zero': l['supplier_diagnostic'].update(break_denominator=0,broken_limit_rate=None)
    if mutation=='missing': l['pools']['broken']['status']='unavailable'
    if mutation=='bad_ratio': l['supplier_diagnostic']['broken_limit_rate']=.9
    if mutation=='overlap': l['overlap_codes']=['a']
    if mutation=='pool_provider': l['pools']['broken']['provider']='other'
    if mutation=='pool_date': l['pools']['broken']['trade_date']='2026-11-06'
    if mutation=='scope': l['pools']['broken']['scope']='other'
    s=get(run(l=l),'supplier_diagnostic','broken_limit_rate')
    assert s['direction']=='unknown' and s['delta'] is None


def test_zero_promotion_denominator_is_unknown_not_zero():
    l=limits('2026-11-09','2026-11-06');l['promotion_2to3']['supplier_diagnostic'].update(fixed_denominator=0,promoted_count=0,rate=None)
    s=get(run(l=l),'supplier_diagnostic','promotion_2to3')
    assert s['direction']=='unknown' and 'missing_or_zero_denominator' in s['reason_codes']


def test_breadth_count_quality_is_local_to_breadth():
    b=breadth('2026-11-09');b['up']=99
    r=run(b=b)
    assert all(s['direction']=='unknown' for s in r['signals'][:4])
    assert all(s['status']=='diagnostic' for s in r['signals'][8:])


def test_future_outcome_labels_have_no_effect():
    b=breadth('2026-11-09',3);old=breadth('2026-11-06')
    expected=run(b=b,old=old)
    for row in (b,old): row.update(final_height=999,exact_end='2099-01-01',delisting_date='2099-01-01')
    assert run(b=b,old=old)==expected
    from scripts.build_direction_signals import PRICE_FIELDS
    assert not {'final_height','exact_end','delisting_date'}&set(PRICE_FIELDS)


def test_strict_positive_requires_verified_evidence_fixture_only():
    a=limits('2026-11-06','2026-11-05');b=limits('2026-11-09','2026-11-06',3)
    for row,n in ((a,2),(b,3)):
        for pool in row['pools'].values(): pool['scope_verified']=True
        row['reasons']={};row['strict_non_st']['up_count']=n
    s=get(run(l=b,old_l=a),'strict_non_st','limit_up_count')
    assert (s['status'],s['direction'],s['delta'])==('formal','positive',1)


def test_consistently_mixed_pool_scopes_are_still_incompatible():
    a=limits('2026-11-06','2026-11-05');b=limits('2026-11-09','2026-11-06')
    for row in (a,b): row['pools']['broken']['scope']='other'
    s=get(run(l=b,old_l=a),'supplier_diagnostic','broken_limit_rate')
    assert s['direction']=='unknown' and 'up_broken_scope_incompatible' in s['reason_codes']


def test_negative_breadth_counts_rejected_even_when_sums_balance():
    b=breadth('2026-11-09',-1)
    s=get(run(b=b),'observed_breadth','up')
    assert s['direction']=='unknown' and 'breadth_invalid_counts' in s['reason_codes']
