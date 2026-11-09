"""Denominator, date attribution, evidence separation and real-release contracts."""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.research.seasonality_study import analyze, query, summary, contrast


def row(code,month,outcome,height=1,year=2020,cohort='direct'):
    return dict(stock_code=code,security_id=code,cycle_id=f'{code}-{year}-{month}',first_board_date=f'{year}-{month:02d}-01',
        year=year,month=month,outcome=outcome,height=height,exact_height=None,cohort=cohort,board='main',regime='main_10',st='unknown',grade='unknown' if outcome=='unknown' else 'verified')


def test_unknown_empty_and_cumulative_thresholds():
    rows=[row('a',3,'success',7),row('b',3,'failure'),row('c',3,'unknown')]
    s=summary(rows)
    assert s['rate']==.5 and s['effective_n']==2 and s['unknown']==1
    assert s['heights']=={'3':1,'5':1,'7':1,'10':0}
    assert s['unknown_bounds']==[1/3,2/3]
    assert summary([])['first_boards'] is None
    assert summary([])['heights']['5'] is None
    assert summary([row('u',1,'unknown')])['rate'] is None


def test_date_filters_evidence_and_height_do_not_contaminate_denominator():
    rows=[row('a',3,'success',5),row('a',9,'failure'),row('b',3,'unknown'),row('d',3,'success',10,cohort='reconstruction')]
    meta={'snapshot':'fixture','data_end':'2026-11-06','undated':{'direct':4,'reconstruction':0}}
    a=analyze(rows,[],meta,height=5,month=3)
    b=analyze(rows,[],meta,height=10,month=9)
    assert a['summary']=={**b['summary'],'leaders':1}
    assert a['total']==1 and b['total']==0
    assert a['summary']['rate']==.5
    assert a['months'][2]['unknown']==1
    assert a['months'][0]['first_boards'] is None
    assert a['contrasts'][0]['one_cycle_per_stock']['other']['effective_n']==0
    assert a['samples'][0]['first_board_date']=='2020-03-01'
    assert analyze(rows,[],meta,st='confirmed_non_st')['summary']['rate'] is None
    assert contrast(rows[:1],{9})['p_iid'] is None


@pytest.fixture(scope='module')
def release():
    try:return query()
    except FileNotFoundError:pytest.skip('Unified release not installed')


def test_release_reconciliation(release):
    s=release['summary']
    assert s['first_boards']==s['success']+s['failure']+s['unknown']
    assert sum(r['first_boards'] or 0 for r in release['cells'])==s['first_boards']
    assert sum(r['heights']['5'] or 0 for r in release['months'])==s['success']
    assert all(r['first_board_date'][5:7]==f"{r['month']:02d}" for r in release['samples'])
    assert release['meta']['formal_eligible'] is False
    assert query(start_year=2017,end_year=2019)['summary']['first_boards'] is None
    assert query(cohort='reconstruction')['summary']!=s
    assert all(r['st']=='confirmed_st' for r in query(st='confirmed_st',height=1)['samples'])


def test_api_contract_and_invalid_filters(release):
    c=TestClient(app);path='/api/research/dragons/seasonality-study-v1'
    assert c.get(path+'?start_year=2026&end_year=2020').status_code==422
    assert c.get(path+'?month=13').status_code==422
    assert c.get(path+'?st=non_st').status_code==422
    r=c.get(path+'?month=3&height=5&page_size=100').json()
    assert r['summary']==release['summary']
    assert all(x['month']==3 and x['height']>=5 for x in r['samples'])
    assert c.get(path+'?grade=unknown').json()['summary']['effective_n']==0


def test_v07_restored_cycles_remain_failures_in_current_study():
    import json
    from pathlib import Path
    from app.research.unified import UnifiedResearch
    from app.research.seasonality_study import load_release
    base=Path.home()/'Datasets/asharedragon/research/dragon_v07'
    if not (base/'manifest.json').exists():pytest.skip('v0.7 audit unavailable')
    manifest=json.loads((base/'manifest.json').read_text())
    restored=json.loads((base/manifest['dataset']/'restored_240.json').read_text())
    v07=json.loads((base/manifest['dataset']/'samples.json').read_text())
    with UnifiedResearch() as r:rows,_,_=load_release(str(r.path))
    by={(r['stock_code'],r['first_board_date']):r for r in rows if r['cohort']=='direct'}
    wrong=[r['cycle_id'] for r in restored if by[(r['stock_code'],r['first_board_date'])]['outcome']!='failure']
    assert wrong==[], f'{len(wrong)} restored short cycles regressed to unknown'
    for r in v07:
        if r['first_board_date']:
            assert by[(r['stock_code'],r['first_board_date'])]['outcome']==r['category']
    assert len(restored)==240


def test_pinned_study_rejects_different_snapshot(tmp_path):
    from app.research.seasonality_study import validate_release
    with pytest.raises(ValueError,match='Frozen study input changed'):
        validate_release(tmp_path/'snapshots'/'different')


def test_primary_and_legacy_window_sensitivity_are_explicit(release):
    assert release['summary']['failure']==5298
    assert release['summary']['unknown']==2895
    assert release['restored_short_cycles']==240
    assert release['legacy_window_sensitivity']['effective_n']==5134
    assert release['summary']['effective_n']==5374
    assert query(start_year=2026,end_year=2026)['summary']['effective_n']==2
    assert query(grade='unknown')['summary']['rate'] is None
    for year,code,height in [(2023,'603178.SH',14),(2024,'603580.SH',13)]:
        result=query(start_year=year,end_year=year,height=10)
        assert len(result['samples'])==1
        row=result['samples'][0]
        assert (row['stock_code'],row['height'],row['grade'])==(code,height,'verified')
        assert not result['meta']['formal_eligible']
