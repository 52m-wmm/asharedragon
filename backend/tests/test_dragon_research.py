from datetime import date,timedelta
import json
import pytest
from app.research.dragons import cycles,window_features,wilson
from app.research.dragon_queries import Dragons


def fixture_sequence(kinds, heights=None):
    dates=[date(2020,1,2)+timedelta(days=i) for i in range(len(kinds))]
    facts={d:{'classification':k,'in_target':True} for d,k in zip(dates,kinds)}
    events={d:{'consecutive_limit_days':heights[i] if heights else None} for i,(d,k) in enumerate(zip(dates,kinds)) if k=='up'}
    return dates,facts,events


@pytest.mark.parametrize('height',[1,3,5,7,10])
def test_completed_milestones_and_availability(height):
    days,facts,events=fixture_sequence(['not_up']+['up']*height+['not_up'])
    row=cycles('000001.SZ',days,facts,events)[0]
    assert row['cycle_complete'] and row['max_height']==height and row['first_board_date']==days[1]
    assert row['outcome_known_at_utc'].date()==days[-1]
    assert row['progress'][0]['height_asof']==1
    for n in (1,3,5,7,10):assert row[f'reached_{n}']==(height>=n)
    assert not row['formal_eligible']


def test_left_boundary_cannot_be_seeded_by_provider():
    days,facts,events=fixture_sequence(['up','up','not_up'],[1,2,None])
    row=cycles('000001.SZ',days,facts,events)[0]
    assert row['max_height'] is None and row['first_board_date'] is None and row['reached_5'] is None


def test_right_censoring_known_positive_and_unknown_failure():
    days,facts,events=fixture_sequence(['not_up']+['up']*5)
    row=cycles('000001.SZ',days,facts,events)[0]
    assert row['reached_5'] is True and row['reached_7'] is None and row['max_height'] is None


def test_calendar_gap_breaks_knowledge_not_failure():
    days,facts,events=fixture_sequence(['not_up','up','unknown','up','not_up'])
    del facts[days[2]]
    rows=cycles('000001.SZ',days,facts,events)
    assert len(rows)==2 and all(r['max_height'] is None and r['reached_5'] is None for r in rows)


def test_conflicting_height_does_not_create_milestone():
    days,facts,events=fixture_sequence(['not_up']+['up']*5+['not_up'],[None,1,2,3,4,8,None])
    row=cycles('000001.SZ',days,facts,events)[0]
    assert row['observed_height']==4 and row['reached_5'] is None and row['max_height'] is None


def test_missing_event_cannot_be_counted_and_suspension_ends_cycle():
    days,facts,events=fixture_sequence(['not_up','up','up','suspended','up','not_up'])
    del events[days[2]]
    rows=cycles('000001.SZ',days,facts,events)
    assert rows[0]['reached_3'] is None
    assert rows[1]['max_height']==1


def test_future_extension_does_not_rewrite_asof_progress():
    days,facts,events=fixture_sequence(['not_up']+['up']*10+['not_up'])
    early=cycles('000001.SZ',days[:4],facts,events)[0]
    final=cycles('000001.SZ',days,facts,events)[0]
    assert early['progress']==final['progress'][:3] and early['max_height'] is None


def test_window_excludes_missing_and_reference_break():
    rows=[dict(close=10+i,pre_close=9+i,volume=100+i,amount=1000+i) for i in range(21)]
    assert window_features(rows,20)['raw_price_change']==2
    rows[7]['pre_close']=1
    assert window_features(rows,20)['raw_price_change'] is None
    assert window_features(rows,5)['raw_price_change'] is not None
    rows[-1]['close']=None
    assert not window_features(rows,5)['complete'] and window_features(rows,5)['mean_amount'] is None


def test_wilson_unknown_is_not_zero():
    assert wilson(0,0) is None
    assert wilson(0,10)[1]>0 and wilson(10,10)[0]<1


@pytest.fixture
def service(tmp_path):
    snapshot=tmp_path/'snapshots/one';snapshot.mkdir(parents=True)
    rows=[]
    for i,h in enumerate([1,5,None]):
        rows.append(dict(provider='test_provider',cycle_id=str(i),stock_code=f'{i:06d}.SZ',stock_name='样本',first_board_date='2020-12-01',first_observed_date='2020-12-01',
            max_height=h,cycle_complete=h is not None,formal_eligible=False,reached_5=True if i==2 else h>=5,
            circulating_market_cap=100+i,board_type='one_price',industry=None,theme=None,windows={},progress=[],candles=[],feature_provenance={}))
    (snapshot/'cycles.json').write_text(json.dumps(rows));(snapshot/'metadata.json').write_text(json.dumps({'formal_count':0,'strict_accepted':False}))
    (snapshot/'coverage.json').write_text(json.dumps([dict(year=2020,month=12,expected_sessions=23,event_sessions=23,full_month_events=True,full_month_audit=True,strict_accepted=False),dict(year=2020,month=11,expected_sessions=21,event_sessions=0,full_month_events=False,full_month_audit=False,strict_accepted=False)]))
    (tmp_path/'manifest.json').write_text(json.dumps({'dataset':'snapshots/one'}))
    return Dragons(tmp_path)


def test_formal_gate_month_missing_and_height_does_not_select_denominator(service):
    assert service.listing()['data']==[]
    assert all(r['first_boards'] is None for r in service.statistics()['data']['monthly'])
    result=service.statistics(mode='diagnostic',min_height=5)['data']
    assert result['monthly'][0]['first_boards']==2 and result['monthly'][0]['dragon_starts']==1
    assert result['monthly'][1]['first_boards'] is None
    assert result['cohort']['censoring_bounds']==[2/3,2/3]
    assert result['cohort']['known_reached_5']==2


def test_search_filters_sort_and_pagination(service):
    assert len(service.listing(mode='diagnostic',min_height=5)['data'])==1
    data=service.listing(mode='diagnostic',min_height=1,sort='stock_code',descending=False,page_size=1,page=2)['data']
    assert data[0]['stock_code']=='000001.SZ'
    assert len(service.listing(mode='diagnostic',min_height=5,include_unknown=True)['data'])==2
    assert not service.listing(mode='diagnostic',industry='不存在')['data']
    with pytest.raises(ValueError):service.listing(start_year=2026,end_year=2020)


def test_api_validates_filters_and_defaults_formal(service):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api.dragons import dataset
    app.dependency_overrides[dataset]=lambda:service
    try:
        with TestClient(app) as client:
            assert client.get('/api/research/dragons').json()['data']==[]
            r=client.get('/api/research/dragons?mode=diagnostic&min_height=5');assert r.status_code==200 and len(r.json()['data'])==1
            for query in ['min_height=6','page=0','month=13','start_year=2026&end_year=2020','mode=anything']:
                assert client.get('/api/research/dragons?'+query).status_code==422
            assert client.get('/api/research/dragons/stock/000001.SZ').json()['data']==[]
            assert client.get('/api/research/dragons/stock/000001.SZ?mode=diagnostic').json()['data'][0]['max_height']==5
    finally:app.dependency_overrides.pop(dataset,None)


def test_first_event_height_conflict_does_not_emit_empty_cycle():
    days,facts,events=fixture_sequence(['not_up','up','not_up'],[None,3,None])
    assert cycles('000001.SZ',days,facts,events)==[]
    with pytest.raises(ValueError):cycles('000001.SZ',days+days[-1:],facts,events)


def test_provider_without_monthly_coverage_must_not_be_reported_as_zero(service):
    # A successful month for source A is not evidence of coverage for source B.
    row=service.statistics(mode='diagnostic',provider='akshare')['data']['monthly'][0]
    assert row['eligible'] is False
    assert row['first_boards'] is None


def test_formal_flag_cannot_bypass_snapshot_acceptance(service):
    service.rows[0]['formal_eligible']=True
    assert service.listing(mode='formal',min_height=1)['data']==[]


@pytest.mark.parametrize('days',[
    [date(2023,9,27),date(2023,9,28),date(2023,10,9),date(2023,10,10)],
    [date(2023,12,28),date(2023,12,29),date(2024,1,2),date(2024,1,3)],
    [date(2024,2,7),date(2024,2,8),date(2024,2,19),date(2024,2,20)],
])
def test_exchange_calendar_cross_month_year_and_holidays(days):
    facts={d:{'classification':k,'in_target':True} for d,k in zip(days,['not_up','up','up','not_up'])}
    events={d:{'consecutive_limit_days':i+1} for i,d in enumerate(days[1:3])}
    result=cycles('000001.SZ',days,facts,events)[0]
    assert result['max_height']==2 and result['first_board_date']==days[1]
    assert result['progress'][0]['height_asof']==1
    assert result['outcome_known_at_utc'].date()==days[-1]


def test_research_state_requires_all_acceptance_evidence():
    from app.research.dragons import research_state
    record={'first_board_date':'2020-12-01','cycle_complete':True,'max_height':5,'formal_eligible':True}
    assert research_state(record,publication_accepted=True)=='diagnostic'
    record['acceptance']={k:True for k in ('sources_verified','rules_validated','coverage_complete','strict_facts_complete')}
    assert research_state(record,publication_accepted=True)=='verified'
    assert research_state(record)=='diagnostic'
    assert research_state({**record,'first_board_date':None},publication_accepted=True)=='unknown'
    assert research_state({**record,'cycle_complete':False,'max_height':None},publication_accepted=True)=='diagnostic'


def test_source_specific_coverage_preserves_missing_month(service):
    service.coverage[0]['by_provider']={'test_provider':{'full_month_events':True,'full_month_audit':True,'event_sessions':23,'strict_accepted':False}}
    assert service.statistics(mode='diagnostic',provider='test_provider')['data']['monthly'][0]['eligible']
    assert service.statistics(mode='diagnostic',provider='akshare')['data']['monthly'][0]['first_boards'] is None
