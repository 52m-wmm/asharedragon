"""Release contracts plus synthetic reader/API tests; no provider requests."""
import json
from pathlib import Path

import duckdb
from fastapi.testclient import TestClient
import pytest
from app.main import app
from app.api.research import unified_research
from app.research.unified import UnifiedResearch
from app.research.queries import DEFAULT_ROOT


def test_reader_filters_and_api_pagination(tmp_path):
    base = tmp_path / 'research/unified'
    out = base / 'snapshots/test'
    out.mkdir(parents=True)
    (base/'manifest.json').write_text(json.dumps({'dataset':'snapshots/test'}))
    (out/'summary.json').write_text(json.dumps({'version':'test','data_end':'2026-11-06','formal_whole_market_labels':0}))
    with duckdb.connect(str(out/'research.duckdb')) as db:
        db.execute("CREATE TABLE stock_daily AS SELECT * FROM (VALUES ('000001.SZ',DATE '2020-01-02',2020,1),('000002.SZ',DATE '2020-01-02',2020,1),('000001.SZ',DATE '2021-01-04',2021,1)) t(stock_code,trade_date,year,month)")
    with UnifiedResearch(tmp_path) as r:
        assert len(r.daily(year=2020,code='000001.SZ')['data']) == 1
        assert r.daily(year=2020,page=2,page_size=1)['data'][0]['stock_code'] == '000002.SZ'
        assert r.daily(year=2020,code="' OR 1=1 --")['data'] == []
        with pytest.raises(ValueError): r.daily(year=2020,month=13)
        app.dependency_overrides[unified_research] = lambda:r
        try:
            client = TestClient(app)
            result = client.get('/api/research/unified/daily?year=2020&code=000001')
            assert result.status_code == 200
            assert len(result.json()['data']) == 1
            assert client.get('/api/research/unified/daily?year=2020&month=13').status_code == 422
        finally:
            app.dependency_overrides.pop(unified_research)


def test_snapshot_path_cannot_escape(tmp_path):
    base = tmp_path/'research/unified'
    base.mkdir(parents=True)
    (base/'manifest.json').write_text('{"dataset":"../../other"}')
    with pytest.raises(ValueError,match='escaped'): UnifiedResearch(tmp_path)


@pytest.fixture(scope='module')
def release():
    if not (DEFAULT_ROOT/'research/unified/manifest.json').exists():
        pytest.skip('Local immutable release not installed')
    with UnifiedResearch() as r:
        yield r


def test_release_keys_and_relations(release):
    db = release.db
    assert db.execute('SELECT count(*) FROM stock_daily').fetchone()[0] == release.summary['rows']
    assert db.execute('SELECT count(*) FROM (SELECT stock_code,trade_date FROM stock_daily GROUP BY ALL HAVING count(*)>1)').fetchone()[0] == 0
    for table,key in [('events','event_id'),('cycles','cycle_id'),('first_board_samples','sample_id'),('labels','label_id')]:
        assert db.execute(f'SELECT count(*)-count(DISTINCT {key}) FROM {table}').fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM sample_cycles ANTI JOIN first_board_samples USING(sample_id)').fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM labels ANTI JOIN cycles USING(cycle_id)').fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM sample_labels').fetchone()[0] == release.summary['tables']['first_board_samples']


def test_unknown_and_feature_label_isolation(release):
    db = release.db
    cols = {r[0] for r in db.execute('DESCRIBE first_board_samples').fetchall()}
    assert not cols & {'outcome','max_height','right_boundary_date','reached_5','cycle_complete'}
    for table in ('labels','sample_labels'):
        assert db.execute(f"SELECT count(*) FROM {table} WHERE outcome='failure' AND quality_status='unknown'").fetchone()[0] == 0
        assert db.execute(f"SELECT count(*) FROM {table} WHERE formal_eligible").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM stock_daily WHERE price_quality='verified' AND pe_ttm IS NULL").fetchone()[0] > 0
    assert db.execute("SELECT count(*) FROM stock_daily WHERE event_conflict AND (is_limit_up IS NOT NULL OR limit_quality<>'unknown')").fetchone()[0] == 0
    for field in ('price_quality','limit_quality','st_quality','valuation_quality','sentiment_quality'):
        assert db.execute(f"SELECT count(*) FROM stock_daily WHERE {field} IS NULL OR {field} NOT IN ('verified','diagnostic','unknown')").fetchone()[0] == 0


def test_latest_evidence_and_preboard_features(release):
    db = release.db
    assert db.execute('SELECT count(*) FROM cycles').fetchone()[0] == release.summary['tables']['cycles']
    assert db.execute("SELECT count(*) FROM labels WHERE quality_status='verified'").fetchone()[0] > 0
    assert db.execute("SELECT count(*) FROM labels WHERE outcome='failure' AND category<>'confirmed_failure_below5'").fetchone()[0] == 0
    cols = {r[0] for r in db.execute('DESCRIBE preboard_features').fetchall()}
    assert not cols & {'outcome','outcome_evidence','primary_included','exact_max_streak'}
    rows = db.execute('SELECT first_board_date, feature_provenance FROM preboard_features').fetchall()
    for first, provenance in rows:
        for value in provenance.values():
            if value and value.get('window_end'):
                assert value['window_end'] < first
