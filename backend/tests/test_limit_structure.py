from datetime import date

import pytest

from app.analytics.limit_structure import pool, promotion, structure

PREV=date(2026,11,9)
DAY=date(2026,11,10)


def event(code,level,day=DAY,kind='up',**extra):
    return dict(stock_code=code,consecutive_limit_days=level,trade_date=day,provider='akshare',
                limit_type=kind,is_sealed_at_close=kind=='up',raw_id=1 if day==PREV else 2) | extra


def source(events,day=DAY,kind='up',**extra):
    return pool('akshare',day,kind,events,dict(status='success',row_count=len(events)),
                scope_verified=True,empty_verified=True,**extra)


def fact(**changes):
    return dict(identity_status='confirmed_non_st',price_reason='valid',trade_status=True,
                event_conflict=False,rule_confirmed=True,height_confirmed=True,is_limit_up=True) | changes


def scenario(level=2):
    old=source([event('600001.SH',level,PREV),event('600002.SH',level,PREV)],PREV)
    today=source([event('600001.SH',level+1),event('600003.SH',level+1)])
    prev_facts={c:fact() for c in ('600001.SH','600002.SH')}
    current={'600001.SH':fact(),'600002.SH':fact(is_limit_up=False),'600003.SH':fact()}
    return old,today,prev_facts,current


@pytest.mark.parametrize('level',[1,2])
def test_stock_intersection_not_today_total_and_normal_strict_rate(level):
    args=scenario(level)
    result=promotion(*args,level,PREV)
    assert result['status']=='confirmed' and result['fixed_denominator']==2
    assert result['promoted_count']==1 and result['rate']==.5
    assert result['counts']['confirmed_not_promoted']==1
    assert result['supplier_diagnostic']['promoted_count']==1
    assert result['supplier_diagnostic']['outside_previous_cohort_codes']==['600003.SH']
    assert [m['stock_code'] for m in result['members']]==['600001.SH','600002.SH']


@pytest.mark.parametrize('level',[1,2])
@pytest.mark.parametrize('change,state',[
    ({'price_reason':'reference_missing'},'price_missing'),
    ({'trade_status':False},'suspended'),
    ({'identity_status':'unknown'},'identity_unknown'),
    ({'identity_status':'confirmed_st'},'identity_unknown'),
    ({'event_conflict':True},'boundary_unknown'),
])
def test_today_unknown_never_shrinks_fixed_queue_or_becomes_failure(level,change,state):
    old,today,prev_facts,current=scenario(level)
    current['600002.SH'].update(change)
    result=promotion(old,today,prev_facts,current,level,PREV)
    assert result['fixed_denominator']==2 and result['promoted_count']==1
    assert result['rate'] is None and result['unresolved_count']==1
    assert result['lower_bound']==.5 and result['upper_bound']==1
    assert result['members'][1]['state']==state
    if change.get('identity_status')=='confirmed_st':
        assert 'qualification_changed' in result['members'][1]['reasons']
        assert result['members'][1]['identity_status']=='confirmed_st'


@pytest.mark.parametrize('change',[{'identity_status':'unknown'},{'height_confirmed':False},{'rule_confirmed':False}])
def test_unverified_yesterday_queue_has_no_formal_denominator_or_bounds(change):
    old,today,prev_facts,current=scenario()
    prev_facts['600002.SH'].update(change)
    result=promotion(old,today,prev_facts,current,2,PREV)
    assert result['fixed_denominator'] is None and result['rate'] is None
    assert result['lower_bound'] is result['upper_bound'] is None
    assert result['supplier_diagnostic']['fixed_denominator']==2


def test_missing_today_preserves_yesterday_queue_and_cannot_use_absence_as_failure():
    old,today,prev_facts,current=scenario()
    today['records']=None
    r=promotion(old,today,prev_facts,current,2,PREV)
    assert r['fixed_denominator']==2 and r['unresolved_count']==2 and r['rate'] is None
    assert r['supplier_diagnostic']['rate'] is None
    old,today,prev_facts,current=scenario()
    current['600002.SH']['is_limit_up']=None
    r=promotion(old,today,prev_facts,current,2,PREV)
    assert r['members'][1]['state']=='boundary_unknown'


def test_empty_vs_missing_and_zero_denominator():
    empty=source([])
    missing=pool('akshare',DAY,'up',[],{'status':'success','row_count':0})
    assert empty['records']==[] and missing['records'] is None
    result=promotion(source([],PREV),empty,{}, {},2,PREV)
    assert result['fixed_denominator']==0 and result['reason']=='zero_denominator'
    assert result['rate'] is None and result['lower_bound'] is None
    zero=structure(empty,source([],kind='broken'),source([],kind='down'),{})
    assert zero['strict_non_st']['up_count']==0 and zero['strict_non_st']['first_count']==0
    assert zero['strict_non_st']['broken_limit_rate'] is None
    assert structure(missing,source([],kind='broken'),source([],kind='down'),{})['strict_non_st']['up_count'] is None


def test_no_skipping_session_or_mixing_provider():
    old,today,prev_facts,current=scenario()
    result=promotion(old,today,prev_facts,current,2,date(2026,11,6))
    assert result['rate'] is None and result['supplier_diagnostic']['rate'] is None
    today['provider']='another_supplier'
    assert promotion(old,today,prev_facts,current,2,PREV)['fixed_denominator'] is None


def test_pool_integrity_and_overlap_and_reseal():
    e=event('600001.SH',1)
    assert source([e,e])['records'] is None
    assert source([e,event('600002.SH',1,raw_id=3)])['records'] is None
    assert source([event('600001.SH',1,PREV)])['records'] is None
    up=source([dict(e,open_times=3)])  # A resealed up event remains up.
    broken=source([event('600002.SH',None,kind='broken')],kind='broken')
    f={'600001.SH':fact(),'600002.SH':fact(is_limit_up=False)}
    result=structure(up,broken,source([],kind='down'),f)
    assert result['supplier_diagnostic']['broken_limit_rate']==.5
    assert result['strict_non_st']['broken_limit_rate']==.5
    overlap=source([event('600001.SH',None,kind='broken')],kind='broken')
    result=structure(up,overlap,source([],kind='down'),f)
    assert result['overlap_codes']==['600001.SH']
    assert result['strict_non_st']['broken_limit_rate'] is None
    assert result['supplier_diagnostic']['broken_limit_rate'] is None


def test_supplier_scope_and_identity_are_not_assumed_and_heights_are_not_reset_to_one():
    old,today,prev_facts,current=scenario()
    old['scope_verified']=today['scope_verified']=False
    result=promotion(old,today,prev_facts,current,2,PREV)
    assert result['rate'] is None and result['supplier_diagnostic']['rate']==.5
    up=source([event('600001.SH',2)])
    result=structure(up,source([],kind='broken'),source([],kind='down'),{'600001.SH':fact(height_confirmed=False)})
    assert result['strict_non_st']['first_count'] is None
    assert result['strict_non_st']['second_count'] is None
    assert result['supplier_diagnostic']['second_board_count']==1
