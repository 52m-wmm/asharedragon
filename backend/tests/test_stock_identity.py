import pytest
from app.providers.normalize import stock_code

@pytest.mark.parametrize('value',['002432.SZ','sz.002432','SZ002432','002432'])
def test_all_source_codes_are_canonical(value):
    assert stock_code(value)=='002432.SZ'

from datetime import date
from app.research.stock_status import security_id,resolve_security,name_intervals,status_at,risk_from_name


def test_listing_reuse_and_code_change_require_explicit_aliases():
    old=security_id('002432',date(2010,6,10));new=security_id('002432',date(2025,1,1))
    assert old!=new and old==security_id('sz.002432',date(2010,6,10))
    aliases=[dict(stock_code='002432.SZ',security_id=old,valid_from=date(2010,6,10),valid_to=date(2020,1,1)),dict(stock_code='002432.SZ',security_id=new,valid_from=date(2025,1,1),valid_to=None)]
    assert resolve_security('002432',date(2022,1,1),aliases) is None
    aliases.append(dict(stock_code='000957.SZ',security_id=old,valid_from=date(2021,1,1),valid_to=None))
    assert resolve_security('000957',date(2022,1,1),aliases)==old
    aliases.append(dict(aliases[-1],security_id=new))
    assert resolve_security('000957',date(2022,1,1),aliases) is None


def test_effective_dates_not_announcement_and_no_negative_from_partial_history():
    records=[dict(ts_code='002432.SZ',name='公司甲',start_date='20210101',end_date='20211231',ann_date='20201220')]
    partial=name_intervals(records)
    assert partial[0]['valid_from']==date(2021,1,1) and partial[0]['risk_warning_status']=='unknown'
    complete=name_intervals(records,pagination_complete=True)
    assert complete[0]['risk_warning_status']=='none'
    duplicate=name_intervals(records+records,pagination_complete=True)
    assert all(x['verification_status']=='conflict' for x in duplicate)
    with pytest.raises(ValueError):name_intervals([dict(records[0],end_date=None)])


def test_name_not_identity_and_independent_status_conflict():
    sid=security_id('002432',date(2010,6,10));aliases=[dict(stock_code='002432.SZ',security_id=sid,valid_from=date(2010,6,10),valid_to=None)]
    rows=name_intervals([dict(ts_code='002432.SZ',name='ST测试',start_date='20210101',end_date='20210102')],pagination_complete=True)
    rows[0].update(security_id=sid,evidence_id='a')
    assert status_at('sz.002432',date(2021,1,1),rows,aliases)['is_st'] is True
    assert status_at('002432',date(2021,1,3),rows,aliases)['is_st'] is None
    rows.append(dict(rows[0],risk_warning_status='none',evidence_id='b'))
    assert status_at('002432',date(2021,1,1),rows,aliases)['verification_status']=='conflict'
    assert risk_from_name('S*ST测试')=='legacy_st'
    assert risk_from_name('PT测试',complete_interval=True)=='unknown'
    assert risk_from_name('XR测试',complete_interval=True)=='unknown'


def test_pagination_cannot_prove_history_from_an_unverified_limit():
    from app.research.stock_status import pagination_complete
    rows=[{'ts_code':'002432.SZ'}]
    assert not pagination_complete([(0,rows)])
    assert pagination_complete([(0,rows)],declared_total=1)
    assert not pagination_complete([(0,rows)],documented_cap=1)
    assert not pagination_complete([(0,rows),(1,rows)],declared_total=2)
    assert not pagination_complete([(0,rows),(2,[])],documented_cap=1)


@pytest.mark.parametrize('code',['sh.002432','002432.SH','2432','九安医疗'])
def test_invalid_or_name_keys_rejected(code):
    with pytest.raises(ValueError):stock_code(code)


def test_known_lifecycle_outside_dates_are_not_suspension():
    alias=dict(stock_code='002432.SZ',security_id='listing-one',valid_from=date(2010,6,10),valid_to=date(2020,1,1))
    before=status_at('002432',date(2009,1,1),[],[alias]);after=status_at('002432',date(2021,1,1),[],[alias])
    assert before['trading_status']=='not_listed' and after['trading_status']=='delisted'
    assert before['is_st'] is None and after['security_id'] is None
