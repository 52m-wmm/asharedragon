from datetime import date
from build_dragon_coverage import classify


def sample():
    return dict(stock_code='002432.SZ',trade_date=date(2021,11,15),open=10,high=11,low=10,close=11,pre_close=10,adjustment_type='none')


def test_missing_st_not_assumed_normal_or_source_truth():
    evidence=dict(listing_date=date(2010,1,1),listing_session=1000,trade_status=True,special_status='unknown')
    strict,diag,status,_=classify(sample(),evidence,'up')
    assert diag['classification']=='unknown' and status=='unknown'
    evidence['is_st']=False
    strict,diag,status,_=classify(sample(),evidence,'up')
    assert diag['classification']=='up' and status=='unknown'
    assert 'special_session_history_unverified' in diag['assumptions']


def test_independent_truth_conflict_is_unknown_not_rule_validation():
    evidence=dict(listing_date=date(2010,1,1),listing_session=1000,trade_status=True,is_st=False,special_status='regular',reference_verified=True)
    _,diag,status,conflict=classify(sample(),evidence,'not_up')
    assert conflict and status=='unknown' and diag['classification']=='unknown'
    assert classify(sample(),evidence,None)[2]=='unknown'
    assert classify(sample(),evidence,'up')[2]=='confirmed_limit_up'


def test_official_suspension_can_confirm_non_up_without_inventing_price():
    evidence=dict(trade_status=False,official_suspension=True)
    strict,diag,status,conflict=classify(None,evidence)
    assert strict['classification']=='suspended' and status=='confirmed_not_limit_up' and not conflict
    assert classify(None,dict(trade_status=False))[2]=='unknown'
    assert classify(None,dict(evidence,event_present=True),'up')[2]=='unknown'
