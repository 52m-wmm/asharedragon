from datetime import date,datetime,timezone
import pytest
from app.research.study_v07 import cycle_outcome,decision_cutoff,availability


def test_closed_cycle_failure_does_not_require_unrelated_trailing_days():
    s=dict(first_board_date='2020-01-02',observed_min_height=1,exact_max_streak=1,complete_cycle_eligible=True,missing_observation_dates=['2020-01-06'])
    assert cycle_outcome(s)[0]=='failure'
    s.update(complete_cycle_eligible=False,exact_max_streak=None)
    assert cycle_outcome(s)[0]=='unknown'


def test_five_proven_days_do_not_require_final_height_but_require_start():
    s=dict(first_board_date='2020-01-02',observed_min_height=5,exact_max_streak=None,complete_cycle_eligible=False)
    assert cycle_outcome(s)[0]=='success'
    s['first_board_date']=None
    assert cycle_outcome(s)[0]=='unknown'


def test_cutoff_uses_next_exchange_session_including_holidays():
    assert decision_cutoff(date(2020,1,23),[date(2020,1,23),date(2020,2,3)]).isoformat()=='2020-02-03T01:25:00+00:00'
    assert decision_cutoff(date(2020,2,3),[date(2020,2,3)]) is None


def test_backfill_does_not_prove_unavailability_or_publication():
    base=dict(value=1,event_at='2020-01-02T07:00:00+00:00',cutoff='2020-01-03T01:25:00+00:00',fetched_at='2026-01-01T00:00:00+00:00',evidence_id='raw:1')
    r=availability(**base)
    assert r['exploratory_value']==1 and r['strict_value'] is None and r['availability_status']=='historical_availability_unverified'
    r=availability(**base,first_available_at='2020-01-02T08:00:00+00:00',publication_verified=True)
    assert r['strict_value']==1
    assert availability(**base,first_available_at='2020-01-02T08:00:00+00:00')['strict_value'] is None


def test_real_capture_is_upper_bound_not_first_publication():
    r=availability(10,event_at='2020-01-02T07:00:00+00:00',cutoff='2020-01-03T01:25:00+00:00',fetched_at='2020-01-02T08:00:00+00:00',evidence_id='raw:1')
    assert r['strict_value']==10 and r['first_available_at_utc'] is None
    with pytest.raises(ValueError):availability(1,event_at=datetime(2020,1,2),cutoff=datetime.now(timezone.utc))


def test_strict_publication_never_uses_cutoff_or_future_event():
    kwargs=dict(event_at='2020-01-02T07:00:00+00:00',cutoff='2020-01-03T01:25:00+00:00',evidence_id='raw:1',publication_verified=True)
    assert availability(2,**kwargs,first_available_at='2020-01-03T01:25:00+00:00')['strict_value'] is None
    kwargs['event_at']='2020-01-03T07:00:00+00:00'
    assert availability(2,**kwargs,fetched_at='2020-01-03T08:00:00+00:00')['strict_value'] is None


def test_unknown_never_enters_failure_and_strict_never_falls_back():
    from app.research.study_v07 import FEATURES,study_statistics
    rows=[]
    for i,category in enumerate(['success','failure','unknown']):
        rows.append(dict(category=category,outcome=1 if category=='success' else 0 if category=='failure' else None,security_id=str(i),cycle_id=str(i),year=2020,month=1,board='main',regime='test',first_board_date='2020-01-02',exploratory_features={k:1.0 for k in FEATURES},strict_features={k:None for k in FEATURES}))
    strict=study_statistics(rows,'strict',reps=10)
    exploratory=study_statistics(rows,'exploratory',reps=10)
    assert strict['cohort']['n']==2 and strict['cohort']['failure']==1 and strict['unknown']==1
    assert strict['feature_eligible_samples']==0 and exploratory['feature_eligible_samples']==2
    assert all(c['success']['n']==0 for c in strict['comparisons'])
