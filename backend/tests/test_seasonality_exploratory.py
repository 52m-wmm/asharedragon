import pytest
from app.research.seasonality_exploratory import statistics

def row(i,outcome='unknown',start='2021-01-04',cohort='direct',grade='unknown'):
    return dict(cycle_id=str(i),stock_code=f'{i:06}.SZ',security_id=str(i),first_board_date=start,audit_year=2021,start_month=1 if start else None,observation_month=2,board='main',cohort=cohort,evidence_grade=grade,outcome=outcome,observed_height=5 if outcome=='success' else 1,exact_height=None)

def test_unknown_bounds_and_undated_are_not_failures_or_starts():
    r=statistics([row(1,'success',grade='verified'),row(2,'failure',grade='verified'),row(3),row(4,start=None)])
    m=r['months'][0];assert m['effective_denominator']==2 and m['promotion_rate']==.5
    assert m['missing_outcome_bounds']==pytest.approx([1/3,2/3])
    assert m['all_undated_worst_case_bounds']==[.25,.75]
    assert r['months'][1]['dated_first_boards']==0 and r['months'][1]['promotion_rate'] is None
    assert r['summary']['undated']==1

def test_populations_never_pool_and_formal_stays_empty():
    rows=[row(1,'success',grade='verified'),row(2,'failure',cohort='reconstruction',grade='diagnostic')]
    assert statistics(rows)['summary']['success']==1
    assert statistics(rows,'reconstruction')['summary']['failure']==1
    assert statistics(rows,grade='diagnostic')['summary']['candidates']==0
    assert statistics(rows,view='formal')['months']==[]
    with pytest.raises(ValueError):statistics(rows,start_year=2025,end_year=2021)

def test_lower_bound_not_exact_height():
    r=statistics([row(1,'success',grade='verified')])['months'][0]
    assert r['observed_ge']['5']==1 and r['exact_ge']['5']==0

def test_api_validates_filters_and_keeps_formal_view(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.research import seasonality_queries
    c=TestClient(app);monkeypatch.setattr(seasonality_queries,'ROOT',tmp_path)
    assert c.get('/api/research/dragons/seasonality-exploratory').status_code==503
    assert c.get('/api/research/dragons/seasonality-exploratory?grade=made_up').status_code==422
    assert c.get('/api/research/dragons/seasonality-exploratory?start_year=2026&end_year=2021').status_code==422
    monkeypatch.setattr(seasonality_queries,'query',lambda *a,**kwargs: {'data':statistics([],a[0],a[1],a[2],a[3],a[4],a[5])})
    assert c.get('/api/research/dragons/seasonality-exploratory?view=formal').json()['data']['months']==[]
