import json
from datetime import date,timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from app.research.queries import Research


@pytest.fixture
def research_data(tmp_path):
    research=tmp_path/'research';research.mkdir()
    dataset=tmp_path/'processed/source';dataset.mkdir(parents=True)
    dates=[date(2026,1,1)+timedelta(days=i) for i in range(59)]
    dates=[d for d in dates if d.weekday()<5]
    pq.write_table(pa.Table.from_pylist([{'trade_date':d} for d in dates]),research/'calendar.parquet')
    pq.write_table(pa.Table.from_pylist([
        {'stock_code':'000001.SZ','listing_date':date(2026,1,5),'delisting_date':None},
        {'stock_code':'000957.SZ','listing_date':date(2026,1,1),'delisting_date':date(2026,1,15)}],
        schema=pa.schema([('stock_code',pa.string()),('listing_date',pa.date32()),('delisting_date',pa.date32())])),research/'stocks.parquet')
    pq.write_table(pa.Table.from_pylist([{'stock_code':'000001.SZ','trade_date':date(2026,2,2),'limit_type':'up','consecutive_limit_days':1,'provider':'tushare_proxy'}]),research/'events.parquet')
    pq.write_table(pa.Table.from_pylist([{'stock_code':'000001.SZ','trade_date':date(2026,1,20),'trade_status':False,'is_st':False,'provider':'baostock'}]),research/'status_evidence.parquet')
    bars=[{'stock_code':'000001.SZ','trade_date':d,'open':10.,'high':11.,'low':9.,'close':10.,'volume':100.,'amount':1000.,'source':'fixture','adjustment_type':'none'}
          for d in dates if date(2026,1,5)<=d<date(2026,2,1) and d!=date(2026,1,20)]
    pq.write_table(pa.Table.from_pylist(bars),dataset/'daily.parquet')
    (dataset/'manifest.json').write_text(json.dumps({'status':'validated_partial','adjustment_type':'none','months':{'2026-01':{'file':'daily.parquet'}}}))
    (research/'catalog.json').write_text(json.dumps({'datasets':['processed/source'],'start':'2026-01-01','end':'2026-02-28','snapshot_at':'test','lifecycle_evidence':'synthetic test catalogue','event_date_range':{'first':'2026-02-02','last':'2026-02-02'}}))
    with Research(tmp_path) as r:yield r


def test_missing_month_is_null_not_zero(research_data):
    result=research_data.monthly(date(2026,1,1),date(2026,2,28))
    jan,feb=result['data']
    assert jan['coverage_status']=='partial'
    assert feb['rows'] is None and feb['stocks'] is None
    assert feb['covered_days']==0 and feb['coverage_status']=='missing'
    assert result['meta']['bse_status']=='out_of_scope_not_collected'


def test_exchange_window_does_not_skip_gap_or_include_event_day(research_data):
    result=research_data.preboard('000001.SZ',date(2026,2,2))
    assert result['meta']['first_board_confirmed_by_local_tushare']
    assert len(result['data']['20']['rows'])==20
    assert result['data']['10']['features'] is None
    assert date(2026,1,20) in result['data']['10']['missing_dates']
    gap=next(r for r in result['data']['10']['rows'] if r['trade_date']==date(2026,1,20))
    assert gap['trade_status'] is False and gap['close'] is None and gap['status_source']=='baostock'
    assert result['data']['5']['features']['mean_volume']==100
    assert max(x['trade_date'] for x in result['data']['20']['rows'])<date(2026,2,2)


def test_lifecycle_missing_excludes_preipo_and_postdelisting(research_data):
    first=research_data.stock('000001.SZ',date(2026,1,1),date(2026,1,30))
    assert first['meta']['missing_active_dates']==[date(2026,1,20)]
    assert first['meta']['missing_active_details'][0]['reason']=='observed_suspension'
    assert date(2026,1,1) in first['meta']['dates_without_observations']
    ended=research_data.stock('000957.SZ',date(2026,1,1),date(2026,2,28))
    assert max(ended['meta']['missing_active_dates'])==date(2026,1,15)


def test_event_left_join_keeps_missing_daily(research_data):
    result=research_data.events(date(2026,2,1),date(2026,2,28))
    assert result['meta']['total_events']==1
    assert result['data'][0]['close'] is None
    assert result['data'][0]['coverage_status']=='daily_missing'


def test_market_non_session_and_missing_session_differ(research_data):
    assert research_data.market(date(2026,2,7))['meta']['observed_stocks'] is None
    missing=research_data.market(date(2026,2,2))
    assert missing['meta']['coverage_status']=='missing'
    assert missing['meta']['missing_stock_codes']==['000001.SZ']


def test_unknown_stock_and_outside_calendar(research_data):
    result=research_data.stock('688001.SH',date(2026,1,1),date(2026,1,30))
    assert result['meta']['coverage_status']=='unknown_lifecycle'
    with pytest.raises(ValueError,match='calendar range'):
        research_data.monthly(date(2017,1,1),date(2026,2,28))


def test_http_research_routes_return_coverage(research_data):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api.research import research
    app.dependency_overrides[research]=lambda:research_data
    try:
        with TestClient(app) as client:
            response=client.get('/api/research/preboard?code=000001&event_date=2026-02-02')
            assert response.status_code==200
            assert response.json()['data']['10']['features'] is None
            assert client.get('/api/research/market?trade_date=2026-02-02&page=0').status_code==422
            coverage=client.get('/api/research/coverage?start=2026-01-01')
            assert coverage.status_code==200 and coverage.json()['data'][-1]['month']=='2026-02'
    finally:app.dependency_overrides.pop(research,None)
