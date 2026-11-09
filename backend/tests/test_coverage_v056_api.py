import json
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app


def test_sample_endpoint_never_merges_unknown_into_failures(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path))
    root=tmp_path/'Datasets/asharedragon/research/dragon_v056';p=root/'snapshots/test';p.mkdir(parents=True)
    (root/'manifest.json').write_text(json.dumps({'dataset':'snapshots/test','version':'test'}))
    base={'stock_code':'600000.SH','first_board_date':'2020-12-01','first_observed_date':'2020-12-01'}
    (p/'samples.json').write_text(json.dumps([{**base,'category':c} for c in ['confirmed_success_ge5','confirmed_failure_below5','outcome_unknown','first_board_unknown']]))
    client=TestClient(app);res=client.get('/api/research/dragons/first-board-samples?scope=legacy&category=confirmed_failure_below5&code=sh.600000').json()
    assert len(res['data'])==1 and res['data'][0]['category']=='confirmed_failure_below5'
    assert res['meta']['category_counts']['outcome_unknown']==1
    assert not res['meta']['unknown_is_failure'] and not res['meta']['formal_year_statistics_eligible']
    assert client.get('/api/research/dragons/first-board-samples?start_year=2024&end_year=2020').status_code==422
