import json
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app


def test_evidence_api_keeps_audit_separate_and_normalizes_codes(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path))
    root=tmp_path/'Datasets/asharedragon/research/dragon_v054'
    snapshot=root/'snapshots/test';snapshot.mkdir(parents=True)
    (root/'manifest.json').write_text(json.dumps({'dataset':'snapshots/test','version':'test'}))
    (snapshot/'summary.json').write_text(json.dumps({'formal_whole_market_L1_labels':0}))
    (snapshot/'cases.json').write_text(json.dumps([{'stock_code':'002432.SZ'},{'stock_code':'603178.SH'}]))
    client=TestClient(app)
    response=client.get('/api/research/dragons/trading-evidence?code=sz.002432')
    assert response.status_code==200
    data=response.json()
    assert data['cases']==[{'stock_code':'002432.SZ'}]
    assert data['meta']['formal_whole_market_eligible'] is False
    assert client.get('/api/research/dragons/trading-evidence?code=九安医疗').status_code==422
    (root/'manifest.json').unlink()
    assert client.get('/api/research/dragons/trading-evidence').status_code==503
