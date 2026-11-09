from datetime import date
import pytest
from app.research.historical_state import normalize_status,resolve_state
from app.research.dragons import cycles

D=date(2021,11,15)

def obs(st='0',trade='1',identifier='raw:1'):
    return normalize_status(dict(code='sz.002432',date=str(D),isST=st,tradestatus=trade),source='baostock',observed_at='2026-11-07T00:00:00+00:00',evidence_id=identifier)


def test_dated_state_preserves_zeros_and_missing():
    a=obs();assert a['stock_code']=='002432.SZ' and a['is_st'] is False
    assert resolve_state([a],D)['state']=='normal_trading'
    assert resolve_state([],D)['state']=='unknown'
    assert resolve_state([obs(st='',trade='')],D)['is_st'] is None
    assert resolve_state([obs('1')],D)['state']=='risk_warning'
    assert resolve_state([obs(trade='0')],D)['state']=='suspended'


def test_conflicting_sources_do_not_overwrite_and_lifecycle_is_not_suspension():
    result=resolve_state([obs(),obs('1',identifier='raw:2')],D)
    assert result['quality']=='conflict' and result['is_st'] is None
    assert len(result['evidence_ids'])==2
    assert resolve_state([],D,listing_date=date(2022,1,1))['state']=='outside_lifecycle'
    assert resolve_state([obs()],D,listing_date=date(2022,1,1))['conflict']


def test_invalid_vendor_flag_rejected():
    with pytest.raises(ValueError):obs('ST')


def test_price_only_population_is_explicit_and_never_formal():
    dates=[date(2023,9,27),date(2023,9,28),date(2023,10,9),date(2023,10,10)]
    facts={d:dict(classification=k,in_target=True) for d,k in zip(dates,['not_up','up','up','not_up'])}
    assert cycles('603178.SH',dates,facts,{})==[]
    candidate=cycles('603178.SH',dates,facts,{},require_event=False)[0]
    assert candidate['max_height']==2 and candidate['formal_eligible'] is False
    facts[dates[2]]['classification']='unknown'
    candidate=cycles('603178.SH',dates,facts,{},require_event=False)[0]
    assert candidate['max_height'] is None and candidate['reached_3'] is None


def test_reference_requires_same_day_independent_ohlc_and_never_changes_input():
    from app.research.historical_state import reference_evidence
    bar=dict(open=10,high=11,low=10,close=11,pre_close=None)
    ref=dict(open=10.,high=11.,low=10.,close=11.,pre_close=10.,evidence_id='cached:1')
    working,verified,conflict,ids=reference_evidence(bar,[ref])
    assert working['pre_close']==10 and bar['pre_close'] is None and verified and not conflict and ids==['cached:1']
    assert reference_evidence(bar,[dict(ref,close=10.9)])[2]
    assert reference_evidence(dict(bar,pre_close=9),[ref])[2]
    assert reference_evidence(bar,[])[1] is False
