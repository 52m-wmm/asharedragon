import json
from datetime import date
import duckdb
from app.services.data_inventory import parquet_inventory,inventory
from app.collectors.ingest import save_raw,ingest_pool
from app.models import TradingDay


def test_files_read_actual_rows_and_report_missing_partition(tmp_path):
    root=tmp_path/'processed/stocks/daily/purchased_test';root.mkdir(parents=True)
    db=duckdb.connect()
    db.execute("CREATE TABLE bars AS SELECT '600537.SH' stock_code,DATE '2026-01-05' trade_date,1.0 close UNION ALL SELECT '600537.SH',DATE '2026-01-05',1.0")
    db.execute('COPY bars TO ? (FORMAT PARQUET)',[str(root/'daily.parquet')]);db.close()
    (root/'manifest.json').write_text(json.dumps({'source':'purchased_test','months':{'2026-01':{'file':'daily.parquet','rows':999},'2026-02':{'file':'absent.parquet','rows':100}}}))
    item=parquet_inventory(tmp_path)[0]
    assert item['rows']==2 and item['duplicate_rows']==1
    assert item['missing_files']==['2026-02']
    assert item['months'][0]['missing_st']==2
    assert item['staged_rows'] is None


def test_inventory_keeps_latest_whole_pool_and_raw_versions_separate(db,tmp_path):
    class Provider:
        name='akshare'
        def __init__(self,records):self.records=records
        def fetch(self,*args,**kw):return {'records':self.records,'columns':['代码','名称']}
    row={'代码':'600000','名称':'样本','最新价':10,'连板数':1}
    day=date(2024,1,3)
    ingest_pool(db,Provider([row,dict(row,代码='600001')]),day,'up')
    ingest_pool(db,Provider([row]),day,'up',force=True)
    save_raw(db,'akshare','stock_zt_pool_em',day,{'archive_only':True},{'records':[dict(row,代码='600002')]},'success')
    db.commit()
    result=inventory(db,tmp_path)
    item=next(x for x in result['items'] if x['id']=='pg:akshare:limitup_events:up')
    raw=next(x for x in result['items'] if x['id']=='raw:akshare:stock_zt_pool_em')
    assert item['rows']==1 and item['stocks']==1
    assert raw['raw_rows']==4 and raw['versions']==3
    assert item['missing_days'] is None
