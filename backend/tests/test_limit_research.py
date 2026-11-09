from datetime import date, timedelta
from decimal import Decimal
import pytest
from app.research.limit_rules import reconstruct, score, l1_labels, reconcile_event, FACTOR_POLICY, require_certified_factors

D=date(2020,12,1)
def row(**kw):
    r=dict(stock_code='600000.SH',trade_date=D,adjustment_type='none',pre_close='10.05',open='10.10',high='11.06',low='10.00',close='11.06')
    r.update(kw);return r

def evidence(**kw):
    e=dict(trade_status=True,is_st=False,special_status='regular',listing_date=date(1999,11,10),listing_session=5000,reference_verified=True)
    e.update(kw);return e

@pytest.mark.parametrize('code,day,st,price',[
    ('600000.SH',date(2025,6,27),True,'10.55'),
    ('600000.SH',date(2026,7,3),True,'10.55'),
    ('600000.SH',date(2026,7,6),True,'11.06'),
    ('300001.SZ',date(2020,8,21),True,'10.55'),
    ('300001.SZ',date(2020,8,21),False,'11.06'),
    ('300001.SZ',date(2020,8,24),True,'12.06'),
    ('688001.SH',date(2024,1,2),True,'12.06'),
])
def test_historical_rate_boundaries(code,day,st,price):
    f=reconstruct(row(stock_code=code,trade_date=day,high=price,close=price),evidence(is_st=st))
    assert f['classification']=='up' and f['limit_price']==Decimal(price) and f['formal_eligible']

@pytest.mark.parametrize('change,reason',[
    ({'trade_status':None},'historical_trading_status_missing'),
    ({'is_st':None},'historical_st_missing'),
    ({'special_status':'unknown'},'special_session_history_unverified'),
    ({'reference_verified':False},'ex_reference_unverified'),
    ({'listing_date':None},'listing_date_missing'),
    ({'listing_session':None},'listing_session_missing'),
    ({'conflict':True},'source_conflict'),
    ({'special_status':'resumption_unverified'},'special_session_requires_official_price'),
    ({'delisting_date':D+timedelta(days=15)},'delisting_window_requires_official_price'),
])
def test_missing_evidence_does_not_guess(change,reason):
    f=reconstruct(row(),evidence(**change))
    assert f['classification']=='unknown' and f['reason']==reason and not f['formal_eligible']

def test_diagnostic_is_never_formal_and_factor_never_used():
    f=reconstruct(row(adj_factor=0),evidence(special_status='unknown',reference_verified=False),diagnostic=True)
    assert f['classification']=='up' and not f['formal_eligible'] and len(f['assumptions'])==2
    assert FACTOR_POLICY['raw_ohlc_allowed'] and not FACTOR_POLICY['formal_adjusted_returns_allowed']
    with pytest.raises(ValueError,match='quarantined'):require_certified_factors()

def test_reference_is_ex_price_not_previous_close():
    f=reconstruct(row(pre_close='10',previous_close='20',open='10',high='11',low='10',close='11'),evidence())
    assert f['classification']=='up'
    f=reconstruct(row(pre_close='.01',open='.01',low='.01',high='.02',close='.02'),evidence())
    assert f['limit_price']==Decimal('.02')

def test_official_price_priority_conflicts_and_high_guard():
    e=evidence(is_st=None,special_status='unknown',reference_verified=False,
               official_prices=[{'value':'11.07','source':'independent_stk_limit'}])
    f=reconstruct(row(high='11.07',close='11.07'),e)
    assert f['classification']=='up' and f['reason']=='official_limit'
    assert reconstruct(row(high='11.08'),e)['reason']=='ohlc_exceeds_limit'
    e['official_prices'].append({'value':'11.08','source':'other_source'})
    assert reconstruct(row(),e)['reason']=='official_price_conflict'
    e['official_prices']=[{'value':'NaN','source':'bad'}]
    assert reconstruct(row(),e)['classification']=='unknown'

def test_ipo_no_limit_and_legacy_first_day():
    e=evidence(listing_date=date(2023,4,10),listing_session=5)
    assert reconstruct(row(trade_date=date(2023,4,14)),e)['classification']=='no_limit'
    e['listing_session']=6
    assert reconstruct(row(trade_date=date(2023,4,17)),e)['classification']=='up'
    assert reconstruct(row(),evidence(listing_session=1))['classification']=='unknown'
    assert reconstruct(row(),evidence(special_status='no_limit'))['classification']=='no_limit'
    assert reconstruct(row(),evidence(special_status='no_limit',official_prices=[{'value':'11.06','source':'x'}]))['classification']=='unknown'

def test_suspension_and_event_disagreement():
    f=reconstruct(row(),evidence(trade_status=False))
    assert f['classification']=='suspended'
    assert reconcile_event(f,{'limit_type':'up'})['status']=='unknown'
    assert reconstruct(row(),evidence(trade_status=False,event_present=True))['classification']=='unknown'
    assert reconcile_event({'classification':'unknown'},{'limit_type':'up'})['status']=='observed_up'

def test_metrics_unknown_not_negative_and_missing_reference_not_negative():
    facts={k:{'classification':v} for k,v in [('a','up'),('b','up'),('c','not_up'),('d','unknown'),('e','up')]}
    m=score(facts,{'a':True,'b':False,'c':True,'d':True})
    assert (m['tp'],m['fp'],m['fn'],m['unknown'],m['unknown_positive'])==(1,1,1,1,1)
    assert m['precision']==.5 and m['recall']==.5 and m['positive_capture']==1/3
    assert score({}, {'a':True})['recall'] is None

def series(kinds):
    dates=[D+timedelta(days=i) for i in range(len(kinds))]
    return dates,[dict(stock_code='600000.SH',trade_date=d,classification=k,formal_eligible=k!='unknown') for d,k in zip(dates,kinds)]

def test_l1_milestones_and_asof_cycle_start():
    dates,f=series(['not_up']+['up']*11)
    labels=l1_labels(f,dates,coverage_complete=True,rules_validated=True)
    assert [x['height'] for x in labels]==list(range(1,12))
    assert all(x['cycle_start']==dates[1] for x in labels)
    assert labels[0]['tags']==['L1_1'] and labels[1]['tags']==['L1_2'] and labels[2]['tags']==['L1_3']
    assert labels[6]['tags']==['L1_5_plus','L1_7_plus']
    assert labels[9]['tags']==['L1_5_plus','L1_7_plus','L1_10_plus']
    assert l1_labels(f,dates,coverage_complete=True,rules_validated=False)==[]

def test_l1_left_censor_missing_or_assumed_interval_cannot_publish():
    dates,f=series(['up','up','not_up','up'])
    labels=l1_labels(f,dates,coverage_complete=True,rules_validated=True)
    assert len(labels)==1 and labels[0]['height']==1 and labels[0]['cycle_start']==dates[-1]
    assert l1_labels(f[1:],dates,coverage_complete=True,rules_validated=True)==[]
    f[-1]['formal_eligible']=False
    assert l1_labels(f,dates,coverage_complete=True,rules_validated=True)==[]

def test_l1_rejects_mixed_stocks_and_duplicate_dates():
    dates,f=series(['not_up','up'])
    with pytest.raises(ValueError):l1_labels(f+[f[0]],dates)
    f[1]['stock_code']='000001.SZ'
    with pytest.raises(ValueError):l1_labels(f,dates)

def test_legacy_s_is_not_st_and_still_has_five_percent_limit():
    f=reconstruct(row(pre_close='15.40',open='15.50',low='15.40',high='16.17',close='16.17'),
                  evidence(is_st=False,special_status='legacy_non_reform'))
    assert f['classification']=='up' and f['rate']==Decimal('.05')

def test_official_limit_requires_provenance():
    assert reconstruct(row(),evidence(official_prices=[{'value':'11.06'}]))['reason']=='official_price_provenance_missing'

def test_official_quote_cannot_override_verified_ipo_no_limit():
    f=reconstruct(row(trade_date=date(2023,4,14)),evidence(listing_date=date(2023,4,10),listing_session=5,
                  official_prices=[{'value':'11.06','source':'contradictory_cache'}]))
    assert f['classification']=='unknown' and f['reason']=='no_limit_price_conflict'


def test_verified_suspension_does_not_require_a_price_bar():
    from app.research.limit_rules import reconstruct
    evidence={'trade_status':False,'special_status':'suspended','status_source':'verified_exchange_notice'}
    assert reconstruct(None,evidence)['classification']=='suspended'
    assert reconstruct(None,{**evidence,'event_present':True})['classification']=='unknown'
    assert reconstruct(None,{})['classification']=='unknown'
    assert reconstruct(None,{**evidence,'conflict':True})['classification']=='unknown'
