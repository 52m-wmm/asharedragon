from datetime import date
import json

import duckdb
import pytest
import extend_daily_2026 as extension


def payload(**changes):
    row={'日期':'2026-03-16','股票代码':'000001','开盘':10,'最高':11,'最低':9,'收盘':10.5,'成交量':100,'成交额':105000}
    row.update(changes)
    return {'records':[row]}


def test_units_status_and_nonoverlap():
    result=extension.normalize(payload(),'000001.SZ',{date(2026,3,16)})
    assert result[0]['volume']==10000
    assert result[0]['amount']==105000
    assert result[0]['trade_status'] is None and result[0]['is_st'] is None
    assert result[0]['adjustment_type']=='none'
    assert extension.normalize(payload(日期='2026-03-13'),'000001.SZ',set())==[]


@pytest.mark.parametrize('changes',[{'股票代码':'600000'},{'成交量':-1},{'收盘':12},{'成交额':None}])
def test_invalid_symbol_or_prices_and_units_rejected(changes):
    with pytest.raises(ValueError):extension.normalize(payload(**changes),'000001.SZ',{date(2026,3,16)})


def test_duplicate_and_non_session_rejected():
    p=payload();p['records']*=2
    with pytest.raises(ValueError):extension.normalize(p,'000001.SZ',{date(2026,3,16)})
    with pytest.raises(ValueError):extension.normalize(payload(),'000001.SZ',set())


def test_publish_roundtrip_preserves_null_status(tmp_path,monkeypatch):
    monkeypatch.setattr(extension,'ROOT',tmp_path)
    monkeypatch.setattr(extension,'LOCAL',tmp_path)
    stage=tmp_path/'stage';(stage/'symbols').mkdir(parents=True)
    (stage/'progress.json').write_text('{}')
    (tmp_path/'sample_gate.json').write_text('{"passed":true}')
    normalized=extension.normalize(payload(),'000001.SZ',{date(2026,3,16)})
    (stage/'symbols/000001.SZ.json').write_text(json.dumps({'code':'000001.SZ','source':extension.SOURCE,'rows':normalized,'raw':payload()}))
    extension.publish(stage)
    destination=tmp_path/'processed/stocks/daily'/extension.SOURCE
    manifest=json.loads((destination/'manifest.json').read_text())
    assert manifest['months']['2026-03']['rows']==1
    with duckdb.connect() as db:
        rows=db.read_parquet(str(destination/'year=2026/month=03/daily.parquet')).fetchall()
        assert len(rows)==1
        table=db.read_parquet(str(destination/'year=2026/month=03/daily.parquet')).to_arrow_table()
        r=table.to_pylist()[0]
        assert r['trade_date']==date(2026,3,16)
        assert r['stock_code']=='000001.SZ' and r['pre_close'] is None and r['trade_status'] is None
