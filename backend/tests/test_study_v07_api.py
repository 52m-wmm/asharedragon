from fastapi.testclient import TestClient
from app.main import app
from app.research import study_v07_queries


def test_v07_routes_preserve_mode_and_reject_invalid_filters(monkeypatch,tmp_path):
    client=TestClient(app)
    monkeypatch.setattr(study_v07_queries,'ROOT',tmp_path)
    assert client.get('/api/research/dragons/study-v07').status_code==503
    assert client.get('/api/research/dragons/study-v07?mode=formal').status_code==422
    assert client.get('/api/research/dragons/study-v07?start_year=2026&end_year=2020').status_code==422
    assert client.get('/api/research/dragons/study-v07/samples?category=unknown').status_code==503
    assert client.get('/api/research/dragons/study-v07/samples?page_size=1000').status_code==422
    calls=[]
    monkeypatch.setattr(study_v07_queries,'query',lambda *args,**kwargs: calls.append(args) or {'data':{'mode':args[0]}})
    assert client.get('/api/research/dragons/study-v07?mode=strict&start_year=2021&end_year=2025').json()['data']['mode']=='strict'
    assert calls[0][:3]==('strict',2021,2025)
