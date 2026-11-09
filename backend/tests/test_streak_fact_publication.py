"""An observed five-day run must be queryable without complete-cycle approval."""
import json
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app


def test_confirmed_streak_is_published_independently(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path))
    root=tmp_path/'Datasets/asharedragon/research/dragon_facts'
    snap=root/'snapshots/test';snap.mkdir(parents=True)
    (root/'manifest.json').write_text(json.dumps({'dataset':'snapshots/test','version':'test'}))
    row={'stock_code':'600000.SH','fact_id':'test','observed_min_height':5,'first_observed_date':'2023-09-28','last_observed_date':'2023-10-12',
         'exact_max_streak':None,'first_board_date':None,'cycle_end_date':None,'evidence_status':'confirmed_lower_bound',
         'complete_cycle_eligible':False,'blockers':[{'field':'first_board_date','reason':'left_boundary_unknown'}]}
    (snap/'facts.json').write_text(json.dumps([row]))
    (snap/'summary.json').write_text(json.dumps({'version':'test'}))
    r=TestClient(app).get('/api/research/dragons/observed-streaks?scope=legacy&min_height=5')
    assert r.status_code==200
    assert r.json()['data'][0]['observed_min_height']==5
    assert r.json()['data'][0]['exact_max_streak'] is None
    assert r.json()['meta']['formal_statistics_eligible'] is False
