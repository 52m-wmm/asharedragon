from datetime import date
from zipfile import ZipInfo

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from import_historical_daily import member_date, normalize, quality


def sample(**changes):
    row = dict(code='000957.SZ', date='20240102', open=10., high=11., low=9.,
               close=10.5, vol=10000., amount=105000., pre_close=10., adj_factor=2., vwap=10.5)
    row.update(changes)
    return pa.Table.from_pylist([row])


@pytest.mark.parametrize('name', ['/tmp/evil.parquet', '../evil.parquet', '1d_price/../../evil',
                                  '1d_price\\20240102.parquet', '1d_price/run.py'])
def test_zip_rejects_unsafe_or_unknown_names(name):
    with pytest.raises(ValueError):
        member_date(ZipInfo(name))


def test_preserves_code_and_refuses_to_infer_adjustment():
    with duckdb.connect() as db:
        result = normalize(sample(), date(2024, 1, 2), 'test_source', db).to_pylist()[0]
    assert result['stock_code'] == '000957.SZ'
    assert result['volume'] == 10000
    assert result['adjustment_type'] == 'unknown'


@pytest.mark.parametrize('changes', [{'date':'20240103'}, {'code':'000001.SH'}, {'code':'510300.SH'}, {'code':957}])
def test_rejects_wrong_date_index_fund_and_numeric_code(changes):
    with duckdb.connect() as db, pytest.raises(ValueError):
        normalize(sample(**changes), date(2024, 1, 2), 'test_source', db)


def test_quality_detects_duplicates_nonfinite_and_nulls(tmp_path):
    with duckdb.connect() as db:
        t = normalize(sample(close=float('inf'), amount=None), date(2024,1,2), 'test_source', db)
        path = tmp_path/'test.parquet'
        pq.write_table(pa.concat_tables([t,t]),path)
        q = quality(db,path)
        assert q['duplicate_keys'] == 1
        assert q['invalid_ohlcv_rows'] == 2
        assert q['nulls']['amount'] == 2


def test_research_rejects_staging_and_path_escape(tmp_path):
    import json
    from app.research.daily import connect_daily
    manifest = tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'status':'staging_unverified'}))
    with pytest.raises(ValueError,match='validated'):
        connect_daily(tmp_path)
    manifest.write_text(json.dumps({'status':'validated_partial','adjustment_type':'none',
        'months':{'2024-01':{'file':'../outside.parquet'}}}))
    with pytest.raises(ValueError,match='Invalid or missing'):
        connect_daily(tmp_path)


def test_release_rejects_wrong_audit_fingerprint(tmp_path):
    import json
    from release_historical_daily import release
    (tmp_path/'manifest.json').write_text(json.dumps({'sha256':'source-one'}))
    (tmp_path/'audit.json').write_text(json.dumps({'archive_sha256':'source-two'}))
    with pytest.raises(ValueError,match='mismatched audit'):
        release(tmp_path,tmp_path/'processed')
    assert not (tmp_path/'processed').exists()
