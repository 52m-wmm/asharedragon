from copy import deepcopy
import pytest
from app.analytics.reference_repair import reference_check,bridge,repair_reference,confirmed_cohort
from app.analytics.matched_cohort_breadth import matched_breadth,POLICY


def evidence(provider='akshare',close=10,ref=10,**kw):
    return dict(provider=provider,open=close,high=close,low=close,close=close,reference_price=ref,
                valid=True,adjustment='none',price_unit='CNY',**kw)


def row(close=10,ref=10,source='akshare'):
    return dict(open=close,high=close,low=close,close=close,pre_close=ref,reference_price=ref,price_conflict=False,
                price_source=source,price_reason='valid',reference_basis='same_response_reference_pct_checked',
                identity_status='unknown',daily_return=close/ref-1)


def test_purchase_pct_is_fraction_and_ak_pct_is_percentage():
    assert reference_check(dict(close=8.88,pre_close=8.64,change=.24,pct_chg=.0278),'purchased')[0]==8.64
    assert reference_check({'收盘':8.88,'涨跌额':.24,'涨跌幅':2.78},'akshare')[0]==8.64
    assert reference_check(dict(close=8.88,pre_close=8.64,change=.24,pct_chg=2.78),'purchased')[0] is None


@pytest.mark.parametrize('raw',[dict(close=10,pre_close=0,change=10,pct_chg=1),dict(close=10,pre_close=9,change=0,pct_chg=.1111),dict(close=10,pre_close=9,change=1,pct_chg=.2),dict(close=10,adj_factor=2)])
def test_inconsistent_missing_or_factor_only_cannot_supply_reference(raw):
    assert reference_check(raw,'purchased')[0] is None


def test_provider_file_to_pg_change_can_be_explained_even_across_corporate_reference_change():
    a,b=row(),row(close=9.9,ref=9.8)
    assert bridge(a,b,evidence(),evidence(close=9.9,ref=9.8))['status']=='resolved'


def test_cross_provider_anchor_and_unexplained_break_are_distinct():
    a=row(source='purchased');old=evidence('purchased')
    assert bridge(a,row(close=10.2),old,evidence(close=10.2))['reason']=='cross_provider_same_unadjusted_units_and_native_reference_anchor'
    b=row(close=9.9,ref=9.8)
    assert bridge(a,b,old,evidence(close=9.9,ref=9.8))['status']=='unknown'
    assert bridge(a,b,old,evidence(close=9.9,ref=9.8),[evidence()])['reason']=='cross_provider_same_day_OHLC_and_reference_overlap'


def test_compatible_price_disagreement_cannot_be_voted_away():
    assert bridge(row(),row(),evidence(),evidence(),[evidence(),evidence('baostock',close=11)])['status']=='conflict'


def test_missing_raw_and_adjustment_mismatch_do_not_pass():
    assert bridge(row(),row(),None,evidence())['status']=='unknown'
    bad=evidence();bad['adjustment']='qfq'
    assert bridge(row(),row(),bad,evidence())['reason']=='adjustment_or_price_unit_incompatible'


def test_small_reference_repair_does_not_lag_fill_or_require_second_provider():
    r=row();r.update(root_cause='supplied_reference_differs_previous_close',prior_price_verified=True)
    assert repair_reference(r,evidence('purchased'))['field_status']=='single_source_validated'
    assert repair_reference(r,evidence('purchased'),[evidence('baostock')])['field_status']=='corroborated'
    assert repair_reference(r,evidence('purchased'),[evidence('baostock',close=11)])['status']=='conflict'
    r['root_cause']='previous_stock_record_missing'
    assert repair_reference(r,evidence('purchased'))['status']=='unknown'
    r['root_cause']='research_start_prior_outside_U'
    assert repair_reference(r,evidence('purchased'),prior_raw_close=10)['status']=='repaired'
    assert repair_reference(r,evidence('purchased'),prior_raw_close=None)['status']=='unknown'


def profile(day,source,returns):
    stocks={c:row(close=10*(1+v),source=source) for c,v in returns.items()}
    return dict(trade_date=day,source_snapshot='U-fixed',breadth_rule_version='E1-fixed',scope=POLICY['scope'],stocks=stocks)


def test_no_unresolved_common_stock_may_be_deleted_to_pass_day():
    a=profile('2026-11-06','file',{'A':.1,'B':-.1});b=profile('2026-11-09','pg',{'A':.2,'B':.1})
    old=matched_breadth(b['trade_date'],a['trade_date'],a,b,snapshot_id='D1-fixed',input_snapshots={})
    p={'A':{'status':'resolved'},'B':{'status':'unknown'}}
    result=confirmed_cohort(old,a,b,p)
    assert result['common_count']==2 and result['calculable'] is False and result['delta'] is None
    assert result['unresolved_common_codes']==['B']


@pytest.mark.parametrize('left,right',[
    ({'A':.1,'B':-.1},{'A':.2,'B':.1}),
    ({'A':0,'B':-.1},{'A':0,'B':-.1}),
    ({'A':.1,'B':-.1,'C':.1},{'A':-.1,'B':.1,'D':.1}),
    ({'A':.1,'B':-.1,'C':-.1},{'A':.2,'B':.1,'D':-.1}),
])
def test_resolved_bridges_retain_original_D1_math_and_composition_sensitivity(left,right):
    a=profile('2026-11-06','file',left);b=profile('2026-11-09','pg',right)
    old=matched_breadth(b['trade_date'],a['trade_date'],a,b,snapshot_id='D1-fixed',input_snapshots={})
    proofs={c:{'status':'resolved'} for c in left.keys()&right.keys()}
    actual=confirmed_cohort(old,a,b,proofs)
    same=deepcopy(b)
    for v in same['stocks'].values():v['price_source']='file'
    expected=matched_breadth(b['trade_date'],a['trade_date'],a,same,snapshot_id='control',input_snapshots={})
    for field in ['common_count','calculable','direction','cohort_direction','delta','value','previous_value','status','reason_codes']:
        assert actual[field]==expected[field]
    assert a['stocks']['A']['price_source']=='file' and b['stocks']['A']['price_source']=='pg'


def test_newly_available_start_date_does_not_compare_to_fabricated_zero():
    from app.analytics.reference_repair import ratio_delta_pp
    assert ratio_delta_pp(None,.4) is None
    assert ratio_delta_pp(.4,None) is None
    assert ratio_delta_pp(0,.4)==40
