import json
import hashlib
import duckdb
from app.services.data_inventory import parquet_inventory,inventory,raw_hash_method,snapshot_inventory,RELEASES


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



class MemoryDB:
    """Exercise production SELECTs against isolated in-memory fixtures, never PostgreSQL."""
    def __init__(self):
        self.db=duckdb.connect()
        self.db.execute("CREATE MACRO json_typeof(v) AS lower(json_type(v))")
        self.db.execute("CREATE MACRO to_char(v, pattern) AS strftime(v, '%Y-%m')")
        self.db.execute('CREATE TABLE raw_observations(id INTEGER,provider VARCHAR,endpoint VARCHAR,trade_date DATE,fetched_at TIMESTAMP,ingestion_status VARCHAR,parameters JSON,raw_payload JSON,sha256 VARCHAR)')
        self.db.execute('CREATE TABLE limitup_events(id INTEGER,raw_id INTEGER,provider VARCHAR,endpoint VARCHAR,trade_date DATE,limit_type VARCHAR,stock_code VARCHAR,ingestion_status VARCHAR)')
        self.db.execute('CREATE TABLE stock_daily(id INTEGER,provider VARCHAR,trade_date DATE,stock_code VARCHAR,adjustment VARCHAR,pre_close DOUBLE,is_st BOOLEAN)')
        self.db.execute('CREATE TABLE trading_days(trade_date DATE,exchange VARCHAR)')

    def execute(self,statement,params=None):
        sql=str(statement).replace(':p','$p').replace(':g','$g')
        result=self.db.execute(sql,params or {})
        columns=[c[0] for c in result.description];rows=result.fetchall()
        class Result:
            def mappings(self):
                return [dict(zip(columns,[json.loads(v) if c in ('parameters','raw_payload') else v for c,v in zip(columns,r)])) for r in rows]
            def scalar(self):return rows[0][0]
            def __iter__(self):return iter(rows)
        return Result()

    def pool(self,raw_id,kind,codes,provider='tushare_proxy',status='success',normalized=True,day='2020-01-02'):
        endpoint='limit_list_d' if provider=='tushare_proxy' else 'stock_zt_pool_em'
        payload={'records':[{'code':c} for c in codes]}
        value=dict(params={},payload=payload,status=status)
        digest=hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.db.execute("INSERT INTO raw_observations VALUES (?,?,?,?::DATE,TIMESTAMP '2026-01-01'+?*INTERVAL '1 second',?,'{}',?,?)",[raw_id,provider,endpoint,day,raw_id,status,json.dumps(payload),digest])
        if normalized:
            for i,code in enumerate(codes):self.db.execute('INSERT INTO limitup_events VALUES (?,?,?,?,?,?,?,?)',[raw_id*100+i,raw_id,provider,endpoint,day,kind,code,status])


def test_inventory_keeps_categories_latest_whole_batches_and_raw_versions(tmp_path):
    db=MemoryDB()
    try:
        db.pool(1,'up',['A','B']);db.pool(2,'down',['C']);db.pool(3,'broken',['D','E'])
        db.pool(4,'up',['A'])  # B must not reappear through per-stock stitching.
        db.pool(5,'up',['F'],normalized=False)
        db.pool(6,'down',['G'],status='failed')
        db.pool(7,'broken',[])
        db.pool(8,'up',['X','Y'],provider='akshare')
        db.pool(9,'up',['X'],provider='akshare')
        result=inventory(db,tmp_path)
        items={x['id']:x for x in result['items']}
        assert [items[f'pg:tushare_proxy:limitup_events:{k}']['rows'] for k in ['up','down','broken']]==[1,1,2]
        assert items['pg:akshare:limitup_events:up']['rows']==1
        raw=items['raw:tushare_proxy:limit_list_d']
        assert raw['versions']==7 and raw['raw_rows']==8 and raw['failures']==1
        assert raw['hash_method_counts']=={'params_payload_status':7}
        assert items['pg:tushare_proxy:limitup_events:up']['missing_days'] is None
        assert len(result['snapshots'])==6
        assert all(n['status']=='unavailable' for n in result['snapshots'])
    finally:db.db.close()


def test_batch_selection_is_independent_across_dates_and_timestamp_ties(tmp_path):
    db=MemoryDB()
    try:
        db.pool(1,'up',['A','B']);db.pool(2,'up',['C'])
        db.db.execute('UPDATE raw_observations SET fetched_at=TIMESTAMP \'2026-01-01\'')
        db.pool(3,'up',['D'],day='2020-01-03')
        item=next(x for x in inventory(db,tmp_path)['items'] if x['id']=='pg:tushare_proxy:limitup_events:up')
        assert item['rows']==2 and item['stocks']==2
    finally:db.db.close()


def test_raw_hash_scopes_are_not_conflated():
    for scope,value in [('params_payload_status',dict(params={},payload={'records':[]},status='success')),('legacy_params_payload',dict(params={},payload={'records':[]}))]:
        digest=hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        assert raw_hash_method({}, {'records':[]}, 'success',digest)==scope
    assert raw_hash_method({}, {},'success','bad')=='unmatched'


def release(root,key,summary=None,inputs=None):
    base=root/RELEASES[key];path=base/'snapshots/test-v1';path.mkdir(parents=True)
    raw=json.dumps(summary or {'version':'test-rule'}).encode();(path/'summary.json').write_bytes(raw)
    (path/'inputs.json').write_text(json.dumps(inputs or {}))
    (base/'manifest.json').write_text(json.dumps({'dataset':'snapshots/test-v1','summary_sha256':hashlib.sha256(raw).hexdigest()}))
    return path


def test_snapshots_preserve_parent_versions_and_field_specific_quality(tmp_path):
    parent=release(tmp_path,'U',{'version':'u1','rows':10,'quality':{'price_quality':[{'status':'verified','n':9}],'st_quality':[{'status':'unknown','n':10}]},'field_coverage':{'official_limit_up_price':0,'official_limit_down_price':0}})
    release(tmp_path,'E1',{'version':'e1','totals':{'observed_count':10,'price_valid_count':8,'price_unknown_count':2}}, {'source_snapshot':str(parent)})
    nodes={n['id']:n for n in snapshot_inventory(tmp_path)}
    u=nodes['U'];e=nodes['E1']
    assert u['code_commit'] is None and u['generated_at'] is None
    assert [f['available_count'] for f in u['fields']]==[9,None,0,0]
    assert e['parents'][0]['matches_current_pointer'] is True
    assert e['fields'][0]['available_count']==8
    assert u['integrity']=='summary_hash_checked'
    old=parent.parent/'old';old.mkdir()
    (tmp_path/RELEASES['E1']/'snapshots/test-v1/inputs.json').write_text(json.dumps({'source_snapshot':str(old)}))
    e=next(n for n in snapshot_inventory(tmp_path) if n['id']=='E1')
    assert e['parents'][0]['snapshot_id']=='old' and not e['parents'][0]['matches_current_pointer']
    old.rmdir()
    e=next(n for n in snapshot_inventory(tmp_path) if n['id']=='E1')
    assert e['status']=='dependency_missing' and not e['fields'][0]['allowed_metrics']


def test_hash_mismatch_missing_metadata_and_path_escape_fail_closed(tmp_path):
    path=release(tmp_path,'U')
    (path/'summary.json').write_text('{}')
    n=snapshot_inventory(tmp_path)[0]
    assert n['status']=='unavailable' and n['reason']=='summary_hash_mismatch'
    (tmp_path/RELEASES['U']/'manifest.json').write_text(json.dumps({'dataset':'../../../outside'}))
    assert snapshot_inventory(tmp_path)[0]['reason']=='snapshot_path_outside_root'
