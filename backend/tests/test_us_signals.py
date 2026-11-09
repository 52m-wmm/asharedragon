from datetime import date,datetime,timedelta,timezone
import math

import pytest
from app.research.us_signals import (us_schedule,align_session,decision_cutoff,price_returns,pool_metrics,aware_utc)
from app.research.us_experiment import walk_forward

UTC=timezone.utc


@pytest.fixture(scope='module')
def schedule():return us_schedule('2020-01-01','2026-11-07')


def bars_for(schedule,seen=None):
    return {r['us_trade_date']:{**r,'source_observed_at_utc':seen or r['assumed_available_at_utc'],
        'price_return':.01,'cash_entitlement_return_proxy':.011,'return_quality':'checked_close_pair'} for r in schedule}


def test_dst_early_close_and_holidays(schedule):
    by_date={r['us_trade_date']:r for r in schedule}
    assert by_date[date(2020,3,6)]['session_close_utc'].hour==21
    assert by_date[date(2020,3,9)]['session_close_utc'].hour==20
    assert by_date[date(2020,11,27)]['session_close_utc'].hour==18
    assert date(2020,11,26) not in by_date
    assert date(2025,1,9) not in by_date  # Carter mourning closure


def test_a_open_uses_prior_us_session_not_same_day_close(schedule):
    row=align_session(date(2026,11,6),schedule,bars_for(schedule))
    assert row['us_trade_date']==date(2026,11,5)
    assert row['session_close_utc']<row['cutoff_utc']
    assert row['cutoff_utc']==datetime(2026,11,6,1,30,tzinfo=UTC)


def test_holiday_alignment_and_no_silent_stale_fallback(schedule):
    bars=bars_for(schedule)
    row=align_session(date(2020,11,30),schedule,bars)
    assert row['us_trade_date']==date(2020,11,27)
    bars.pop(date(2020,11,27))
    row=align_session(date(2020,11,30),schedule,bars)
    assert row['status']=='missing_expected_us_session' and row['price_return'] is None


def test_strict_blocks_future_download_and_explicit_diagnostic_labels(schedule):
    bars=bars_for(schedule,datetime(2026,11,7,tzinfo=UTC))
    strict=align_session(date(2020,12,1),schedule,bars)
    assert strict['price_return'] is None and not strict['pit_verified']
    diagnostic=align_session(date(2020,12,1),schedule,bars,mode='retrospective')
    assert diagnostic['price_return']==.01 and not diagnostic['pit_verified']
    assert diagnostic['status']=='retrospective_unverified_vintage'


def test_exact_cutoff_is_excluded(schedule):
    # 2020-11-27 early close 18:00 UTC + 60 min = 03:00 Shanghai on Nov 28.
    row=align_session(date(2020,11,28),schedule,bars_for(schedule),clock='03:00')
    assert row['us_trade_date']==date(2020,11,25)


def test_naive_timestamps_and_unknown_mode_rejected(schedule):
    with pytest.raises(ValueError,match='Naive'):aware_utc(datetime(2026,1,1))
    with pytest.raises(ValueError):align_session(date(2020,12,1),schedule,bars_for(schedule),mode='unchecked')
    with pytest.raises(ValueError):decision_cutoff(date(2020,12,1),'09:30+08:00')


def test_cash_entitlement_not_price_return_and_no_gap_bridge():
    cal=[{'us_trade_date':date(2020,1,i)} for i in (2,3,6)]
    rows=[{'us_trade_date':date(2020,1,2),'close':100.},{'us_trade_date':date(2020,1,3),'close':99.}]
    result=price_returns(rows,cal,{date(2020,1,3):2.})
    assert result[1]['price_return']==pytest.approx(-.01)
    assert result[1]['cash_entitlement_return_proxy']==pytest.approx(.01)
    rows=[rows[0],{'us_trade_date':date(2020,1,6),'close':102.}]
    assert price_returns(rows,cal,{})[-1]['price_return'] is None


def test_close_disagreement_blocks_two_return_pairs():
    cal=[{'us_trade_date':date(2020,1,i)} for i in (2,3,6,7)]
    rows=[{'us_trade_date':r['us_trade_date'],'close':100.,'close_disagreement':i==1} for i,r in enumerate(cal)]
    result=price_returns(rows,cal,{})
    assert result[1]['price_return'] is None and result[2]['price_return'] is None
    assert result[3]['price_return']==0


def event(code,height):return {'stock_code':code,'consecutive_limit_days':height}


def test_event_missing_is_not_zero_and_industry_never_invented():
    assert pool_metrics(None,[],None)['limit_up_count'] is None
    verified_empty=pool_metrics([],[],None)
    assert verified_empty['limit_up_count']==0 and verified_empty['broken_limit_rate'] is None
    assert verified_empty['tech_limit_up_count'] is None


def test_promotion_uses_same_previous_day_cohort_and_retains_daily_gaps():
    prev=[event('A',1),event('B',1),event('C',2)];today=[event('A',2),event('C',3)]
    measured=pool_metrics(today,[],prev)
    assert measured['second_board_promotion_rate']==.5 and measured['third_board_promotion_rate']==1
    missing=pool_metrics(today,[],prev,missing_today=['B'])
    assert missing['second_board_promotion_rate'] is None
    assert missing['third_board_promotion_rate']==1
    assert pool_metrics(today,[],None)['second_board_promotion_rate'] is None


def test_conflicting_events_and_missing_heights():
    assert pool_metrics([event('A',1)],[event('A',None)],None)['event_quality']=='conflicting_pool_membership'
    assert pool_metrics([event('A',None)],[],None)['first_board_count'] is None


def synthetic_diagnostic_rows():
    result=[]
    for i in range(90):
        day=date(2020,1,1)+timedelta(days=i);cutoff=decision_cutoff(day)
        result.append({'trade_date':day,'event_provider':'test','lag_event_provider':'test',
            'lag_limit_up_count':20+i*.2,'lag_first_board_count':10+math.sin(i),
            'lag_broken_limit_rate':.2+.01*math.cos(i),'lag5_mean_up_count':18+i*.19,
            'qqq_price_return':.01*math.sin(i*.3),'limit_up_count':22+i*.21+math.sin(i*.5),
            'decision_cutoff_utc':cutoff,'qqq_assumed_available_at_utc':cutoff-timedelta(hours=3),
            'outcome_available_proxy_utc':cutoff+timedelta(hours=7)})
    return result


def test_walk_forward_never_fits_current_or_future_label():
    rows=synthetic_diagnostic_rows();summary,before=walk_forward(rows,'limit_up_count')
    assert summary['oos_rows']==30
    assert all(p['last_training_date']<p['trade_date'] for p in before)
    rows[-1]['limit_up_count']+=10000
    _,after=walk_forward(rows,'limit_up_count')
    assert [p['a_plus_qqq'] for p in before]==[p['a_plus_qqq'] for p in after]
    rows[-1]['lag_limit_up_count']+=10000
    _,after_feature=walk_forward(rows,'limit_up_count')
    assert [p['a_only'] for p in before[:-1]]==[p['a_only'] for p in after_feature[:-1]]


def test_future_signal_and_provider_pooling_rejected():
    rows=synthetic_diagnostic_rows();rows[-1]['qqq_assumed_available_at_utc']=rows[-1]['decision_cutoff_utc']
    with pytest.raises(ValueError,match='Future'):walk_forward(rows,'limit_up_count')
    rows=synthetic_diagnostic_rows();rows[-1]['event_provider']='another_scope'
    with pytest.raises(ValueError,match='universes'):walk_forward(rows,'limit_up_count')


def test_small_or_missing_target_not_scored():
    rows=synthetic_diagnostic_rows()
    summary,predictions=walk_forward(rows[:20],'limit_up_count')
    assert summary['status']=='insufficient_history' and not predictions
    assert walk_forward(rows,'tech_limit_up_count')[0]['oos_rows']==0


def test_previous_close_revision_time_also_gates_strict_signal():
    schedule=us_schedule('2020-01-02','2020-01-03')
    rows=[{'us_trade_date':date(2020,1,2),'close':100.,'source_observed_at_utc':datetime(2026,1,1,tzinfo=UTC)},
          {'us_trade_date':date(2020,1,3),'close':101.,'source_observed_at_utc':datetime(2020,1,3,23,tzinfo=UTC)}]
    normalized=price_returns(rows,schedule,{})
    result=align_session(date(2020,1,6),schedule,{r['us_trade_date']:r for r in normalized})
    assert result['price_return'] is None
    assert result['status']=='historical_vintage_not_available_by_cutoff'


def test_parquet_utc_roundtrip_and_default_api_vintage_guard(tmp_path):
    import json
    import pyarrow as pa
    import pyarrow.parquet as pq
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api.us_signals import dataset
    from app.research.us_signals import USSignals,TARGETS
    snapshot=tmp_path/'snapshot';snapshot.mkdir()
    (tmp_path/'manifest.json').write_text(json.dumps({'dataset':'snapshot','ashare_start':'2020-01-01','ashare_end':'2020-01-06'}))
    cal=us_schedule('2020-01-02','2020-01-03')
    bars=list(bars_for(cal,datetime(2026,1,1,tzinfo=UTC)).values())
    record={'trade_date':date(2020,1,6),**{k:None for k in TARGETS},'limit_up_count':10,
        'outcome_available_proxy_utc':datetime(2020,1,6,8,30,tzinfo=UTC),
        'event_provider':'fixture','event_quality':'provider_pool_complete',
        'promotion_quality':'missing_previous_pool','daily_missing_count':1,'industry_quality':'unverified',
        'event_source_observed_at_utc':datetime(2026,1,1,tzinfo=UTC),
        'lag_source_observed_at_utc':datetime(2026,1,1,tzinfo=UTC),'lag_limit_up_count':8,
        'lag_first_board_count':4,'lag_broken_limit_rate':.2,'lag5_mean_up_count':8.,'lag_event_provider':'fixture'}
    for name,rows in [('qqq',bars),('us_calendar',cal),('sentiment',[record])]:
        pq.write_table(pa.Table.from_pylist(rows),snapshot/f'{name}.parquet')
    with USSignals(tmp_path) as service:
        app.dependency_overrides[dataset]=lambda:service
        try:
            with TestClient(app) as client:
                url='/api/research/us/association?start=2020-01-06&end=2020-01-06'
                response=client.get(url);assert response.status_code==200
                row=response.json()['data'][0]
                assert row['features']['qqq']['price_return'] is None
                assert row['features']['ashare_lags']['lag_limit_up_count'] is None
                assert row['quality']['daily_missing_count']==1
                assert row['outcomes']['limit_up_count']==10 and row['outcomes_are_not_preopen_features']
                r=client.get(url+'&mode=retrospective').json()['data'][0]
                assert r['features']['qqq']['price_return']==.01
                assert datetime.fromisoformat(r['cutoff_utc']).utcoffset()==timedelta(0)
                for field in ('session_open_utc','session_close_utc','assumed_available_at_utc'):
                    assert datetime.fromisoformat(r['features']['qqq'][field]).utcoffset()==timedelta(0)
                assert client.get(url+'&decision_time=25:00').status_code==422
                assert client.get('/api/research/us/nq').json()['data'] is None
        finally:app.dependency_overrides.pop(dataset,None)
