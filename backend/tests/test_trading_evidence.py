from datetime import date,timedelta
from app.research.trading_evidence import trading_evidence,limit_evidence,streak_fields

D=date(2021,11,15);CODE='002432.SZ'

def bar(**overrides):
    return dict(stock_code=CODE,trade_date=D,open=10,high=11,low=10,close=11,volume=1000,amount=10500,pre_close=10,source='audited',adjustment_type='none',**overrides)


def trading(row,**kwargs):
    return trading_evidence(CODE,D,row,trusted=True,is_session=True,**kwargs)


def test_turnover_proves_trading_not_st_or_limit_up():
    row=bar();t=trading(row)
    assert t['state']=='traded_observed' and t['raw_trade_status'] is None
    ev=dict(listing_date=date(2010,6,10),listing_session=1000,is_st=None,special_status='unknown')
    assert limit_evidence(row,ev,t)['status']=='unknown'
    source=dict(stock_code=CODE,trade_date=D,is_limit_up=True,trusted=True,evidence_id='raw:1')
    assert limit_evidence(row,ev,t,[source])['status']=='confirmed_limit_up'
    assert ev['is_st'] is None and 'trade_status' not in ev


def test_zero_missing_and_invalid_dates_never_imply_suspension():
    for row in (None,{**bar(),'volume':0,'amount':0},{**bar(),'trade_date':D+timedelta(days=1)},{**bar(),'amount':1}):
        assert trading(row)['state']=='trading_unknown'
    assert trading(None,suspension_ids=['notice:1'])['state']=='suspended_confirmed'
    assert trading(None,lifecycle_trusted=True,listing_date=date(2022,1,1))['state']=='not_listed_confirmed'


def test_positive_volume_conflicting_with_suspension_is_quarantined():
    t=trading(bar(),raw_trade_status=False)
    assert t['conflict'] and t['state']=='trading_unknown'
    assert limit_evidence(bar(),{},t)['status']=='unknown'


def test_conflicting_events_and_close_disagreement_block():
    row=bar();t=trading(row);e=dict(stock_code=CODE,trade_date=D,is_limit_up=True,trusted=True,evidence_id='a')
    assert limit_evidence(row,{},t,[e,dict(e,is_limit_up=False,evidence_id='b')])['quality']=='conflict'
    assert limit_evidence(row,{},t,[dict(e,close=10)])['quality']=='conflict'
    assert limit_evidence(row,{},t,[dict(e,trade_date=D+timedelta(days=1))])['status']=='unknown'


def test_lower_bound_survives_both_unknown_boundaries_and_missing_sessions():
    ds=[D+timedelta(days=i) for i in range(7)]
    facts={d:dict(status='confirmed_limit_up',evidence_ids=[str(d)]) for d in ds[1:6]}
    result=streak_fields(CODE,ds,facts)[0]
    assert result['fields']['observed_streak_ge_5']['value'] is True
    assert result['fields']['first_board_date']['value'] is None
    assert result['fields']['exact_max_streak']['value'] is None
    assert result['fields']['cycle_end_date']['value'] is None
    assert result['fields']['observed_streak_ge_7']['value'] is None
    del facts[ds[3]]
    assert all(x['fields']['observed_streak_ge_3']['value'] is None for x in streak_fields(CODE,ds,facts))


def test_end_start_and_exact_height_are_independent():
    ds=[date(2023,9,27),date(2023,9,28),date(2023,10,9),date(2023,10,10)]
    f={d:dict(status=k,evidence_ids=[str(d)]) for d,k in zip(ds,['unknown','confirmed_limit_up','confirmed_limit_up','confirmed_not_limit_up'])}
    result=streak_fields(CODE,ds,f)[0]['fields']
    assert result['cycle_end_date']['value']==ds[2] and result['first_board_date']['value'] is None and result['exact_max_streak']['value'] is None
    f[ds[0]]['status']='confirmed_not_limit_up'
    result=streak_fields(CODE,ds,f)[0]['fields']
    assert result['exact_max_streak']['value']==2 and result['observed_streak_ge_3']['value'] is False


def test_negative_bound_proof_does_not_need_st_but_needs_scoped_rule_and_raw_prices():
    row={**bar(),'close':10.5};t=trading(row)
    policy={'daily_upper_bound_or_no_limit':True,'rule_evidence_id':'exchange:reviewed-rule'}
    result=limit_evidence(row,policy,t)
    assert result['status']=='confirmed_not_limit_up'
    assert result['basis']=='close_below_session_high'
    assert result['numeric_limit_price'] is None
    assert 'exchange:reviewed-rule' in result['evidence_ids']
    assert limit_evidence(row,{},t)['status']=='unknown'
    assert limit_evidence({**row,'adjustment_type':'qfq'},policy,t)['status']=='unknown'
    assert limit_evidence({**row,'close':11},policy,t)['status']=='unknown'
    source=dict(stock_code=CODE,trade_date=D,is_limit_up=True,trusted=True,evidence_id='contradiction')
    assert limit_evidence(row,policy,t,[source])['quality']=='conflict'


def test_price_negatives_close_cycle_without_requiring_st_or_inventing_limit_price():
    ds=[D+timedelta(days=i) for i in range(7)]
    facts={}
    policy={'daily_upper_bound_or_no_limit':True,'rule_evidence_id':'exchange:reviewed-rule'}
    for i,d in enumerate(ds):
        row={**bar(),'trade_date':d,'close':10.5 if i in (0,6) else 11}
        t=trading_evidence(CODE,d,row,trusted=True,is_session=True)
        event=[] if i in (0,6) else [dict(stock_code=CODE,trade_date=d,is_limit_up=True,trusted=True,evidence_id=f'raw:{i}')]
        f=limit_evidence(row,policy,t,event);assert f['numeric_limit_price'] is None
        facts[d]=dict(status=f['status'],evidence_ids=f['evidence_ids'])
    fields=streak_fields(CODE,ds,facts)[0]['fields']
    assert fields['observed_streak_ge_5']['value'] is True
    assert fields['exact_max_streak']['value']==5
    assert fields['first_board_date']['value']==ds[1]
    assert fields['cycle_end_date']['value']==ds[-2]
    assert fields['observed_streak_ge_7']['value'] is False


def test_negative_bound_does_not_override_independent_price_conflicts():
    row={**bar(),'close':10.5};policy={'daily_upper_bound_or_no_limit':True,'rule_evidence_id':'exchange:reviewed-rule',
        'official_prices':[{'value':11,'source':'a'},{'value':10.9,'source':'b'}]}
    assert limit_evidence(row,policy,trading(row))['quality']=='conflict'
    policy['official_prices']=[{'value':10.9,'source':'a'}]
    assert limit_evidence(row,policy,trading(row))['status']=='unknown'
