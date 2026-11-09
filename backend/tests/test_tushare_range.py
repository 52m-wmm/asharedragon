from datetime import date
from unittest.mock import Mock
import pytest
from app.collectors.tushare_range import fetch_ranges,validate,RangeView
from app.providers.tushare_proxy import ProxyError,CONFIG

D=date(2020,12,1);E=date(2020,12,2)
def row(d='20201201',code='000001.SZ'):
    return dict(trade_date=d,ts_code=code,limit='U',limit_times=1)
def payload(rows,params=None):
    return dict(records=rows,params=params or {},columns=['trade_date','ts_code','limit','limit_times'],fetched_at='2026-11-05T00:00:00+00:00',response={'data':rows},archive_path='fixture.json',attempts=[])

def test_range_rejects_wrong_dates_and_duplicate_keys():
    with pytest.raises(ProxyError,match='outside'):validate(payload([row('20201203')]),[D,E])
    with pytest.raises(ProxyError,match='duplicate'):validate(payload([row(),row()]),[D])

def test_near_cap_splits_without_publishing_parent(monkeypatch):
    monkeypatch.setattr('app.collectors.tushare_range.NEAR_CAP',2)
    c=Mock();c.fetch.side_effect=[payload([row(),row('20201202')]),payload([row()]),payload([row('20201202')])]
    out=list(fetch_ranges(c,[D,E]));assert len(out)==2 and c.fetch.call_count==3
    assert out[0][0]==[D] and out[1][0]==[E]

def test_missing_day_is_split_not_marked_complete():
    c=Mock();c.fetch.side_effect=[payload([row()]),payload([row()]),payload([])]
    iterator=fetch_ranges(c,[D,E]);assert next(iterator)[0]==[D]
    with pytest.raises(ProxyError,match='empty_unverified'):next(iterator)

def test_view_preserves_provenance_and_filters_without_network():
    c=Mock(source_id='test',url='http://example.test',config=CONFIG)
    p=payload([row(),row('20201202')],{'start_date':'20201201','end_date':'20201202'})
    v=RangeView(c,p).fetch('limit_list_d',trade_date='20201201',limit_type='D')
    assert v['records']==[] and v['range_validated'] and v['source_archive']=='fixture.json'
    c.fetch.assert_not_called()


def test_interrupted_range_reuses_raw_without_request():
    c=Mock();c.recovery_payload=payload([row(),row('20201202')])
    result=list(fetch_ranges(c,[E]))
    assert result[0][1]['records']==[row('20201202')]
    assert result[0][1]['projection'].startswith('offline')
    c.fetch.assert_not_called()


def test_single_day_near_cap_uses_type_exchange_split(monkeypatch):
    monkeypatch.setattr('app.collectors.tushare_range.NEAR_CAP',2)
    c=Mock();c.config={'limit_list_d':{'cap':2500}}
    c.fetch.return_value=payload([row(),row(code='000002.SZ')])
    merged=payload([row(),row(code='000002.SZ'),row(code='000003.SZ')]);merged['parts']=[]
    split=Mock(return_value=merged);monkeypatch.setattr('app.collectors.tushare_range.complete_payload',split)
    c.save.return_value='split.json'
    out=list(fetch_ranges(c,[D]))
    assert len(out[0][1]['records'])==3 and out[0][1]['archive_path']=='split.json'
    assert c.config['limit_list_d']['cap']==2500
    split.assert_called_once_with(c,'limit_list_d',{'trade_date':'20201201'})
