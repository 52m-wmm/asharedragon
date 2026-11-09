from datetime import date,timedelta
from app.research.trading_evidence import streak_fields
from app.research.coverage_v056 import first_board_sample,boundary_gaps

D=date(2020,12,1);CAL=[D+timedelta(days=i) for i in range(8)]

def run(statuses):
    f={d:{'status':s,'evidence_ids':[str(d)]} for d,s in zip(CAL,statuses)}
    return streak_fields('600000.SH',CAL,f)[0],f


def test_success_does_not_need_end_boundary_but_needs_verified_first_board():
    r,f=run(['confirmed_not_limit_up']+['confirmed_limit_up']*5+['unknown']*2)
    s=first_board_sample(r,CAL,f)
    assert s['category']=='confirmed_success_ge5' and s['observation_window_complete']
    assert not r['exact_cycle_eligible'] and not s['formal_year_statistics_eligible']
    assert not s['local_research_eligible']
    f[CAL[0]]['status']='unknown';r=streak_fields('600000.SH',CAL,f)[0]
    assert first_board_sample(r,CAL,f)['category']=='first_board_unknown'


def test_failure_requires_five_session_observation_window_not_just_cycle_end():
    r,f=run(['confirmed_not_limit_up','confirmed_limit_up','confirmed_not_limit_up']+['unknown']*5)
    assert r['exact_cycle_eligible']
    assert first_board_sample(r,CAL,f)['category']=='outcome_unknown'
    for d in CAL[3:6]:f[d]={'status':'confirmed_not_limit_up'}
    assert first_board_sample(r,CAL,f)['category']=='confirmed_failure_below5'
    assert first_board_sample(r,CAL,f)['local_research_eligible']


def test_gap_interrupts_sequence_and_returns_precise_boundary_dates():
    r,f=run(['unknown','confirmed_limit_up','confirmed_limit_up','unknown','confirmed_limit_up'])
    assert r['observed_length']==2
    gaps=boundary_gaps(r,CAL,f)
    assert [g['trade_date'] for g in gaps]==[CAL[0],CAL[3]]
    assert all(not g['st_required_for_direct_event'] for g in gaps)
