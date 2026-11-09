import hashlib,json
from fastapi.testclient import TestClient
from app.main import app
from app.research import feature_queries


def test_missing_snapshot_and_invalid_filters(tmp_path,monkeypatch):
    monkeypatch.setattr(feature_queries,'ROOT',tmp_path)
    c=TestClient(app)
    assert c.get('/api/research/dragons/feature-study').status_code==503
    assert c.get('/api/research/dragons/feature-study?board=other').status_code==422
    assert c.get('/api/research/dragons/feature-study?month=13').status_code==422


def test_snapshot_integrity_and_empty_filter(tmp_path):
    p=tmp_path/'snapshots/fixed';p.mkdir(parents=True)
    files={}
    for name,data in [('samples.json',[]),('metadata.json',{'snapshot':'fixed'}),('statistics.json',{'n':0})]:
        payload=json.dumps(data).encode();(p/name).write_bytes(payload);files[name]=hashlib.sha256(payload).hexdigest()
    manifest={'dataset':'snapshots/fixed','files':files};(tmp_path/'manifest.json').write_text(json.dumps(manifest))
    assert feature_queries.query(root=tmp_path)['data']=={'n':0}
    assert feature_queries.query(start_year=2021,root=tmp_path)['data']['cohort']['rate'] is None
    feature_queries.load.cache_clear();(p/'samples.json').write_text('[1]')
    import pytest
    with pytest.raises(ValueError,match='checksum'):feature_queries.query(root=tmp_path)
