"""Release isolation guards and real explicit-reader acceptance (opt-in fixture)."""
import inspect
import json
import os
from pathlib import Path
import pytest
from scripts.finalize_research import build,sha
from scripts.prepare_source_release import prepare
from app.research.unified import UnifiedResearch


def test_isolated_full_build_cannot_publish(tmp_path):
    with pytest.raises(ValueError,match='candidate-only'):
        build(tmp_path,output_root=tmp_path/'isolated',candidate_only=False)
    assert not (tmp_path/'isolated').exists()


def test_preparation_rejects_existing_output(tmp_path):
    marker=tmp_path/'existing';marker.write_text('protected')
    with pytest.raises(ValueError,match='new isolated'):
        prepare(tmp_path/'source',tmp_path,tmp_path/'lifecycle.json')
    assert marker.read_text()=='protected'


def test_preparation_rejects_production_root(tmp_path):
    with pytest.raises(ValueError,match='new isolated'):
        prepare(tmp_path,tmp_path,tmp_path/'lifecycle.json')


def test_history_builder_accepts_explicit_root():
    from scripts.build_history_limits import build as history
    assert 'root' in inspect.signature(history).parameters


@pytest.fixture
def release_root():
    value=os.environ.get('ASHARE_RELEASE_CANDIDATE_ROOT')
    if not value:pytest.skip('Explicit isolated release fixture not supplied')
    return Path(value)


def test_complete_candidate_reader_and_file_hashes(release_root):
    m=json.loads((release_root/'research/unified/manifest.json').read_text())
    with UnifiedResearch(release_root,dataset=m['dataset']) as r:
        for name,h in m['files'].items():assert sha(r.path/name)==h
        for name in ['calendar.parquet','stocks.parquet','events.parquet','pg_stock_daily.parquet','research.duckdb','summary.json','inputs.json']:
            assert (r.path/name).exists()
        assert r.db.execute('SELECT count(*) FROM stock_daily').fetchone()[0]==10354849
        sample=r.daily(year=2026,month=11,code='600537.SH')['data']
        row=next(x for x in sample if str(x['trade_date'])=='2026-11-09')
        assert row['price_source']=='akshare'
        assert (row['open'],row['high'],row['low'],row['close'],row['pre_close'])==(2.12,2.25,1.92,1.99,2.05)
        assert r.db.execute('SELECT count(*) FROM (SELECT stock_code,trade_date FROM stock_daily GROUP BY 1,2 HAVING count(*)>1)').fetchone()[0]==0
