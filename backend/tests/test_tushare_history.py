from datetime import date
from decimal import Decimal
from unittest.mock import Mock
import pytest
from sqlalchemy import select,func
from app.providers.tushare_proxy import ProxyClient,ProxyError,parse,CONFIG
from app.collectors.tushare_history import normalize_limit,normalize_aux,complete_payload,new_batch,collect_partition,run_batch
from app.models import Event,ProxyFact,ProxyPartition,Raw,TradingDay

D=date(2020,12,1)
def row(**kw):
    return dict(trade_date='20201201',ts_code='000001.SZ',name='fixture',limit='U',first_time='093000',last_time='145900',
                open_times=0,limit_times=2,fd_amount=12345.67,float_mv=1e9,total_mv=2e9,**kw)

class Fake:
    source_id='test-source';url='http://example.test';used=0
    config=CONFIG
    def __init__(self,records=None):self.records=records if records is not None else [row()];self.fail=False
    def safe(self,v):return str(v)
    def fetch(self,endpoint,refresh=False,**params):
        self.used+=1
        if self.fail:raise ProxyError('network','offline')
        return dict(records=self.records,columns=list(self.records[0]) if self.records else [],params=params,
                    fetched_at='2026-11-05T00:00:00+00:00',attempts=[])


def test_mapping_units_null_and_historical_code():
    r=row();r['limit_amount']=999
    v=normalize_limit(r,D)
    assert v['seal_amount']==Decimal('12345.67')
    assert v['circulating_market_cap']==Decimal('1000000000.0')
    assert v['first_limit_time'].isoformat()=='09:30:00'
    assert v['limit_price'] is None
    r['ts_code']='600001.SH';assert normalize_limit(r,D)['stock_code']=='600001.SH'
    r['trade_date']='20201202'
    with pytest.raises(ValueError):normalize_limit(r,D)
    _,v=normalize_aux('daily_basic',dict(trade_date='20201201',ts_code='000001.SZ',circ_mv=100,total_mv=200,pe_ttm=None),D)
    assert v['circulating_market_cap']=='1000000' and v['pe_ttm'] is None


def test_complete_partition_idempotent_and_failed_refresh_preserves(db):
    c=Fake();b=new_batch(db,c,D,D,['limit_list_d']);params={'trade_date':'20201201'}
    p,n=collect_partition(db,c,b,'limit_list_d',D,params)
    assert p.status=='success' and n==1
    old=p.raw_id
    p,n=collect_partition(db,c,b,'limit_list_d',D,params)
    assert n==0 and c.used==1
    c.fail=True
    p,n=collect_partition(db,c,b,'limit_list_d',D,params,refresh=True)
    assert p.status=='success' and p.raw_id==old and p.error_message
    assert db.scalar(select(func.count()).select_from(Event))==1


def test_conflicting_duplicates_reject_partition(db):
    a=row();b=row();b['fd_amount']=99
    c=Fake([a,b]);batch=new_batch(db,c,D,D,['limit_list_d'])
    p,n=collect_partition(db,c,batch,'limit_list_d',D,{'trade_date':'20201201'})
    assert p.status=='missing' and n==0
    assert db.scalar(select(func.count()).select_from(Event))==0


def test_exact_duplicates_audited_and_empty_not_zero(db):
    c=Fake([row(),row()]);b=new_batch(db,c,D,D,['limit_list_d'])
    p,n=collect_partition(db,c,b,'limit_list_d',D,{'trade_date':'20201201'})
    assert n==1 and p.audit['duplicates']==1
    c.records=[]
    p,n=collect_partition(db,c,b,'stock_st',D,{'trade_date':'20201201'})
    assert p.status=='missing' and p.row_count is None


def test_source_separation_aux_not_sparse_daily(db):
    c=Fake([dict(trade_date='20201201',ts_code='000001.SZ',total_mv=1,circ_mv=2)])
    b=new_batch(db,c,D,D,['daily_basic'])
    p,n=collect_partition(db,c,b,'daily_basic',D,{'trade_date':'20201201'})
    assert p.status=='success' and n==1
    assert db.scalar(select(ProxyFact)).provider=='tushare_proxy'
    assert db.scalar(select(func.count()).select_from(Event))==0


def test_cap_splits_types_and_rejects_leaf_truncation():
    c=Fake();c.config={**CONFIG,'limit_list_d':{**CONFIG['limit_list_d'],'cap':2}}
    def fetch(endpoint,refresh=False,**params):
        records=[row(),row()] if 'limit_type' not in params else [dict(row(),limit=params['limit_type'])]
        return dict(records=records,params=params,fetched_at='2026-11-05T00:00:00+00:00')
    c.fetch=fetch
    p=complete_payload(c,'limit_list_d',{'trade_date':'20201201'})
    assert {r['limit'] for r in p['records']}=={'U','D','Z'} and len(p['parts'])==4
    c.fetch=lambda endpoint,refresh=False,**params:dict(records=[row(),row()],params=params,fetched_at='2026-11-05T00:00:00+00:00')
    with pytest.raises(ProxyError,match='truncated'):complete_payload(c,'limit_list_d',{'trade_date':'20201201'})


def test_ignored_filter_and_wrong_date_rejected():
    c=Fake()
    with pytest.raises(ValueError,match='filter'):complete_payload(c,'limit_list_d',{'trade_date':'20201201','limit_type':'Z'})
    with pytest.raises(ValueError,match='date'):complete_payload(c,'limit_list_d',{'trade_date':'20201202'})


def test_transport_safe_cache_retry_budget_and_no_redirect(tmp_path,monkeypatch):
    client=ProxyClient(url='http://example.test/api',token='fake-test-token',accept_http=True,archive=tmp_path,max_requests=2)
    a=Mock(status_code=429);a.json.return_value={'code':429,'msg':'too many fake-test-token'}
    b=Mock(status_code=200);b.json.return_value={'code':0,'data':{'fields':['ts_code'],'items':[['000001.SZ']]}}
    client.session.post=Mock(side_effect=[a,b]);sleep=Mock();monkeypatch.setattr('app.providers.tushare_proxy.time.sleep',sleep)
    result=client.fetch('daily_basic',trade_date='20201201');assert client.used==2
    assert client.fetch('daily_basic',trade_date='20201201')['records']==result['records']
    assert client.used==2;assert any(c.args[0]>89 for c in sleep.call_args_list)
    assert all('fake-test-token' not in p.read_text() for p in tmp_path.rglob('*.json'))
    assert client.session.post.call_args.kwargs['allow_redirects'] is False
    assert client.session.post.call_args.kwargs['verify'] is True
    with pytest.raises(ProxyError,match='budget'):client.fetch('daily_basic',trade_date='20201202')
    client.max_requests=3;client.session.post=Mock(return_value=Mock(status_code=302))
    with pytest.raises(ProxyError,match='redirect'):client.fetch('daily_basic',trade_date='20201202')


def test_official_endpoint_and_unapproved_http_rejected(tmp_path):
    with pytest.raises(ProxyError):ProxyClient(url='https://api.tushare.pro',token='fake',archive=tmp_path)
    with pytest.raises(ProxyError):ProxyClient(url='http://example.test',token='fake',archive=tmp_path)


def test_batch_endpoint_failure_does_not_block_next(db):
    c=Fake();raw=Raw(provider='fixture',endpoint='calendar',raw_payload={},parameters={},sha256='fixture',ingestion_status='success')
    db.add(raw);db.flush();db.add(TradingDay(trade_date=D,exchange='SSE',source='fixture',raw_id=raw.id));db.commit()
    original=c.fetch
    def fetch(endpoint,**params):
        if endpoint=='kpl_list':raise ProxyError('permission','fixture')
        return original(endpoint,**params)
    c.fetch=fetch
    b=new_batch(db,c,D,D,['kpl_list','limit_list_d'])
    result=run_batch(db,c,b)
    assert result['status']=='partial' and result['inserted']==1
    assert b.progress['limit_list_d'][0]['status']=='success'


def test_upstream_pool_exhausted_retries_after_60_seconds(tmp_path,monkeypatch):
    c=ProxyClient(url='http://example.test/api',token='fake',accept_http=True,archive=tmp_path,max_requests=2)
    a=Mock(status_code=200);a.json.return_value={'code':-1,'msg':'[upstream_pool_exhausted] 数据当前不可用，请稍后重试'}
    b=Mock(status_code=200);b.json.return_value={'code':0,'data':{'fields':['trade_date'],'items':[['20201225']]}}
    c.session.post=Mock(side_effect=[a,b]);sleep=Mock();monkeypatch.setattr('app.providers.tushare_proxy.time.sleep',sleep)
    assert len(c.fetch('limit_list_d',trade_date='20201225')['records'])==1
    sleep.assert_any_call(60)


def test_failed_partition_resume_refreshes_source(db):
    c=Fake([]);b=new_batch(db,c,D,D,['limit_list_d'])
    p,n=collect_partition(db,c,b,'limit_list_d',D,{'trade_date':'20201201'})
    assert p.status=='missing'
    c.records=[row()]
    original=c.fetch;c.fetch=Mock(side_effect=original)
    p,n=collect_partition(db,c,b,'limit_list_d',D,{'trade_date':'20201201'})
    assert p.status=='success' and n==1
    assert c.fetch.call_args.kwargs['refresh'] is True


def test_server_echoed_credential_is_redacted_before_return(tmp_path):
    c=ProxyClient(url='http://example.test/api',token='fixture-private-token',accept_http=True,archive=tmp_path,max_requests=1)
    response=Mock(status_code=200)
    response.json.return_value={'code':0,'data':{'fields':['name'],'items':[['fixture-private-token']]}}
    c.session.post=Mock(return_value=response)
    result=c.fetch('stock_st',trade_date='20201201')
    assert result['records']==[{'name':'[REDACTED]'}]
    assert 'fixture-private-token' not in str(result)


def test_safety_stop_prevents_next_day_requests(db):
    c=Fake([]);raw=Raw(provider='fixture',endpoint='calendar',raw_payload={},parameters={},sha256='safety',ingestion_status='success')
    db.add(raw);db.flush()
    for d in (D,date(2020,12,2)):
        db.add(TradingDay(trade_date=d,exchange='SSE',source='fixture',raw_id=raw.id))
    db.commit()
    b=new_batch(db,c,D,date(2020,12,2),['limit_list_d'],{'safety_stop':True})
    result=run_batch(db,c,b)
    assert result['status']=='paused' and c.used==1
    assert len(b.progress['limit_list_d'])==1


def test_quality_decline_retains_raw_but_not_normalized_facts(db):
    c=Fake([dict(row(),fd_amount=None)]);b=new_batch(db,c,D,D,['limit_list_d'],{'safety_stop':True})
    p,n=collect_partition(db,c,b,'limit_list_d',D,{'trade_date':'20201201'})
    assert p.status=='missing' and 'quality_decline' in p.error_message and n==0
    assert db.scalar(select(func.count()).select_from(Event))==0
    assert db.scalar(select(Raw).where(Raw.ingestion_status=='failed')).raw_payload['records']


def test_ip_quota_respects_server_hint_with_backoff(tmp_path,monkeypatch):
    c=ProxyClient(url='http://example.test/api',token='fake',accept_http=True,archive=tmp_path,max_requests=3)
    limited=Mock(status_code=200);limited.json.return_value={'code':-1,'msg':'[ip_rate_limited] 来源IP额度已达到限制；建议等待30秒后重试'}
    ok=Mock(status_code=200);ok.json.return_value={'code':0,'data':{'fields':['trade_date'],'items':[['20200102']]}}
    c.session.post=Mock(side_effect=[limited,limited,ok]);sleep=Mock();monkeypatch.setattr('app.providers.tushare_proxy.time.sleep',sleep)
    result=c.fetch('limit_list_d',trade_date='20200102')
    assert result['records'] and len(result['attempts'])==3
    assert sum(c.args[0]>89 for c in sleep.call_args_list)==2


def test_capped_valuation_rejects_ignored_offset():
    c=Fake([dict(trade_date='20201201',ts_code='000001.SZ')]);c.config={**CONFIG,'daily_basic':{**CONFIG['daily_basic'],'cap':1}}
    with pytest.raises(ProxyError,match='pagination ignored'):
        complete_payload(c,'daily_basic',{'trade_date':'20201201'})


def test_capped_valuation_paginates_disjoint_rows():
    c=Fake();c.config={**CONFIG,'daily_basic':{**CONFIG['daily_basic'],'cap':1}}
    def fetch(endpoint,**params):
        rows=[dict(trade_date='20201201',ts_code='000001.SZ')] if not params.get('offset') else []
        return dict(records=rows,params={k:v for k,v in params.items() if k!='refresh'},fetched_at='2026-11-05T00:00:00+00:00',attempts=[])
    c.fetch=fetch
    assert len(complete_payload(c,'daily_basic',{'trade_date':'20201201'})['records'])==1


def test_empty_projection_requires_verified_range(db):
    c=Fake([]);b=new_batch(db,c,D,D,['limit_list_d'],{'validated_range':True})
    # A flag alone cannot turn an empty HTTP response into completeness.
    p,n=collect_partition(db,c,b,'limit_list_d',D,{'trade_date':'20201201'})
    assert p.status=='missing'
    original=c.fetch
    def fetch(endpoint,**params):return {**original(endpoint,**params),'range_validated':True,'source_archive':'fixture-range.json'}
    c.fetch=fetch
    p,n=collect_partition(db,c,b,'limit_list_d',D,{'trade_date':'20201201'})
    assert p.status=='success' and p.row_count==0 and n==0


def test_cached_response_hash_mismatch_prevents_reuse(tmp_path):
    import json
    from pathlib import Path
    c=ProxyClient(url='http://example.test/api',token='fake',accept_http=True,archive=tmp_path,max_requests=1)
    response=Mock(status_code=200);response.json.return_value={'code':0,'data':{'fields':['trade_date'],'items':[['20201201']]}}
    c.session.post=Mock(return_value=response)
    p=c.fetch('limit_list_d',trade_date='20201201')
    path=Path(p['archive_path']);stored=json.loads(path.read_text());stored['records'][0]['trade_date']='20201202'
    path.write_text(json.dumps(stored))
    with pytest.raises(ProxyError,match='hash mismatch'):c.fetch('limit_list_d',trade_date='20201201')
    assert c.used==1


def test_invalid_optional_fields_are_null_with_original_evidence(db):
    original=row();original.update(limit_times=0,open_times=-1,first_time='bad-time',fd_amount=-2)
    c=Fake([original]);b=new_batch(db,c,D,D,['limit_list_d'])
    p,n=collect_partition(db,c,b,'limit_list_d',D,{'trade_date':'20201201'})
    assert p.status=='success' and n==1 and p.audit['field_anomaly_count']==4
    e=db.scalar(select(Event).where(Event.raw_id==p.raw_id))
    assert e.consecutive_limit_days is None and e.open_times is None and e.first_limit_time is None and e.seal_amount is None
    assert db.get(Raw,p.raw_id).raw_payload['records'][0]==original
    p,n=collect_partition(db,c,b,'limit_list_d',D,{'trade_date':'20201201'})
    assert n==0 and c.used==1


def test_optional_field_tolerance_does_not_relax_identity():
    for updates in ({'trade_date':'20201202'},{'ts_code':'bad-code'},{'limit':'unknown'}):
        r=row();r.update(updates)
        with pytest.raises(ValueError):normalize_limit(r,D,[])
