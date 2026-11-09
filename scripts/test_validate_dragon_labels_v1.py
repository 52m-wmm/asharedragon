from datetime import date

from validate_dragon_labels_v1 import confusion, sample_year
from app.research.limit_rules import reconstruct
from app.research.trading_evidence import trading_evidence, limit_evidence, streak_fields
from app.research.study_v07 import cycle_outcome


def test_abstention_is_not_false_negative_or_true_negative():
    rows = [dict(truth=True,prediction=None),dict(truth=None,prediction=False)]
    result = confusion(rows)
    assert (result['tp'],result['fp'],result['fn'],result['tn']) == (0,0,0,0)
    assert result['known_positive_abstentions'] == 1
    assert result['truth_unknown'] == 1
    assert result['precision'] is None and result['conditional_recall'] is None
    assert result['positive_capture'] == 0


def test_confusion_reports_denominators_only_for_known_truth_and_prediction():
    rows = [dict(truth=t,prediction=p) for t,p in [(True,True),(False,True),(True,False),(False,False),(True,None)]]
    result = confusion(rows)
    assert (result['tp'],result['fp'],result['fn'],result['tn']) == (1,1,1,1)
    assert result['precision'] == result['conditional_recall'] == .5
    assert result['positive_capture'] == 1/3


def test_missing_st_separates_price_scenario_from_direct_event_not_a_negative():
    day=date(2023,10,9)
    bar=dict(stock_code='603178.SH',trade_date=day,open=11,high=11,low=10.5,close=11,
             pre_close=10,volume=100,amount=1080,adjustment_type='none')
    ev=dict(listing_date=date(2014,2,10),listing_session=2000,trade_status=True,
            is_st=None,special_status='unknown',reference_verified=False)
    old=reconstruct(bar,ev,diagnostic=True)
    assert old['classification']=='unknown' and old['reason']=='historical_st_missing'
    counterfactual=reconstruct(bar,{**ev,'is_st':False},diagnostic=True)
    assert counterfactual['classification']=='up' and not counterfactual['formal_eligible']
    trading=trading_evidence(bar['stock_code'],day,bar,trusted=True,is_session=True)
    event=dict(stock_code=bar['stock_code'],trade_date=day,trusted=True,is_limit_up=True,evidence_id='fixture:independent-event')
    assert limit_evidence(bar,ev,trading,[event])['status']=='confirmed_limit_up'
    assert limit_evidence(bar,ev,trading,[])['status']=='unknown'


def test_holiday_sequence_lower_bound_does_not_invent_first_or_final():
    days=[date.fromisoformat(s) for s in ['2023-09-27','2023-09-28','2023-10-09','2023-10-10','2023-10-11','2023-10-12','2023-10-13']]
    facts={d:dict(status='confirmed_limit_up',evidence_ids=['fixture']) for d in days[1:6]}
    fields=streak_fields('603178.SH',days,facts)[0]['fields']
    assert fields['observed_streak_ge_5']['value'] is True
    assert fields['first_board_date']['value'] is None
    assert fields['exact_max_streak']['value'] is None
    assert cycle_outcome(dict(first_board_date=None,observed_min_height=5))[0]=='unknown'


def test_closed_short_cycle_does_not_require_redundant_future_sessions():
    # v0.7 already fixed this; finite validation must not reintroduce the old 240-row exclusion.
    assert cycle_outcome(dict(first_board_date='2024-03-01',observed_min_height=1,
        exact_max_streak=1,complete_cycle_eligible=True,legacy_missing_observation_dates=['2024-03-05']))[0]=='failure'


def test_year_assignment_uses_first_board_not_warmup_boundary():
    assert sample_year(dict(start=date(2021,12,31),selected={'year':2022}))==2022
