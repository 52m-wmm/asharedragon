"""Synthetic data appears only in isolated test fixtures, never in the dashboard."""
from datetime import date, datetime, timezone
import subprocess
import pytest
from sqlalchemy import select, func, inspect
from fastapi.testclient import TestClient
from app.main import app
from app.db.session import get_db
from app.models import Raw, Event, Coverage, Sentiment, TradingDay, Job
from app.collectors.ingest import ingest_pool,normalize_pool,save_raw,new_job,run_job,recalculate
from app.services.calendar import sessions
from app.providers.client import Client,SourceError
from app.services.queries import latest_events

DATE=date(2024,1,3)
ROW={'代码':'000001','名称':'测试样本','连板数':1,'首次封板时间':'093000','最后封板时间':'145900','成交额':100,'流通市值':1000}
class FixtureProvider:
    name='akshare'
    def __init__(self,records=None,error=None):self.records=[ROW.copy()] if records is None else records;self.error=error
    def fetch(self,endpoint,**params):
        if self.error:raise SourceError(self.error)
        return {'columns':['代码','名称'],'records':self.records}

def calendar(db):
    raw=save_raw(db,'akshare','calendar',None,{}, {'records':[]},'success')
    for d in [date(2023,12,29),date(2024,1,2),DATE,date(2024,1,4),date(2024,1,5),date(2024,1,8)]:
        db.add(TradingDay(trade_date=d,exchange='SSE',source='test-only',raw_id=raw.id))
    db.commit()

def test_migrated_schema(test_engine):
    assert {'stocks','stock_daily','limitup_events','stock_themes','themes','market_sentiment_daily','dragon_labels','ingestion_jobs'}.issubset(set(inspect(test_engine).get_table_names()))

def test_idempotent_and_failed_refresh(db):
    assert ingest_pool(db,FixtureProvider(),DATE,'up')[:2]==(1,1)
    assert ingest_pool(db,FixtureProvider(),DATE,'up',force=True)[:2]==(1,0)
    assert db.scalar(select(func.count()).select_from(Event))==1
    result=ingest_pool(db,FixtureProvider(error='network failure'),DATE,'up',force=True)
    assert result[2]=='network failure'
    assert len(latest_events(db,'akshare',DATE,DATE))==1
    assert db.scalar(select(Coverage)).status=='success'

def test_empty_is_missing(db):
    result=ingest_pool(db,FixtureProvider([]),DATE,'up')
    assert result[2] is not None
    assert db.scalar(select(Coverage)).status=='missing'
    recalculate(db,DATE)
    assert db.get(Sentiment,(DATE,'akshare')).limit_up_count is None

def test_duplicates_and_conflicts(db):
    rows=[dict(ROW,序号=1),dict(ROW,序号=2)]
    assert len(normalize_pool(rows,'up'))==1
    rows[1]['成交额']=999
    result=ingest_pool(db,FixtureProvider(rows),DATE,'up')
    assert '冲突' in result[2]
    assert db.scalar(select(func.count()).select_from(Event))==0

def test_revised_snapshot_does_not_retain_removed_stock(db):
    ingest_pool(db,FixtureProvider([ROW,dict(ROW,代码='600000')]),DATE,'up')
    ingest_pool(db,FixtureProvider([dict(ROW,成交额=200)]),DATE,'up',force=True)
    assert len(latest_events(db,'akshare',DATE,DATE))==1
    assert db.scalar(select(func.count()).select_from(Event))==3

def test_calendar_holidays_and_unknown(db):
    calendar(db)
    assert sessions(db,date(2023,12,30),DATE)==[date(2024,1,2),DATE]
    assert sessions(db,date(2024,1,6),date(2024,1,7))==[]
    with pytest.raises(ValueError,match='日历'):sessions(db,date(2020,1,1),DATE)

def test_partial_job_preserves_other_partitions(db):
    calendar(db)
    class Broken(FixtureProvider):
        def fetch(self,endpoint,**params):
            if endpoint=='stock_zt_pool_zbgc_em':raise SourceError('unavailable')
            return super().fetch(endpoint,**params)
    job=new_job(db,'pools',date(2024,1,2),DATE)
    result=run_job(db,job.job_id,Broken())
    assert result.status=='partial';assert result.rows_inserted==4
    assert db.get(Sentiment,(DATE,'akshare')).broken_limit_rate is None

def test_retry_and_timeout(monkeypatch):
    attempts=[]
    def run(*args,**kwargs):
        attempts.append(1)
        if len(attempts)<3:raise subprocess.TimeoutExpired('source',1)
        return subprocess.CompletedProcess([],0,'{"records":[],"columns":[]}', '')
    monkeypatch.setattr(subprocess,'run',run)
    assert Client(retries=2,interval=0).fetch('endpoint')['records']==[]
    assert len(attempts)==3

def test_api_contract_and_point_in_time(db):
    calendar(db);ingest_pool(db,FixtureProvider(),DATE,'up');recalculate(db,DATE)
    app.dependency_overrides[get_db]=lambda:db
    try:
        with TestClient(app) as api:
            candidate=api.get('/api/events',params={'trade_date':str(DATE)}).json()
            assert candidate['meta']['total']==0 and candidate['meta']['purpose']=='trading'
            r=api.get('/api/events',params={'trade_date':str(DATE),'purpose':'history'}).json();assert r['meta']['total']==1
            assert r['data'][0]['pe_ttm'] is None
            assert api.get('/api/events?trade_date=bad').status_code==422
            assert api.get('/api/events?trade_date=2024-01-03&page=0').status_code==422
            assert api.get('/api/stocks/invalid').status_code==422
            assert api.get('/api/health').json()['data']['database']=='postgresql'
            snapshot=api.get('/api/research/snapshot',params={'code':'000001','trade_date':str(DATE),'as_of':'2099-01-01T16:00:00+08:00'}).json()
            assert snapshot['data']['events']==[] # unknown available_at cannot become a predictive feature
            old=api.get('/api/research/snapshot',params={'code':'000001','trade_date':str(DATE),'as_of':'2024-01-03T16:00:00+08:00','mode':'collected'}).json()
            assert old['data']['events']==[]
            assert api.get('/api/data/status').status_code==200
            assert api.get('/api/seasonality?start_year=2024&end_year=2024').status_code==200
            r=api.post('/api/jobs',json={'start_date':'2024-01-02','end_date':'2024-01-03'});assert r.status_code==202
            assert r.json()['data']['status']=='queued'
    finally:app.dependency_overrides.clear()

def test_seasonality_uses_first_board_month(db):
    calendar(db)
    for d,n in [(date(2023,12,29),1),(date(2024,1,2),2),(DATE,3)]:
        ingest_pool(db,FixtureProvider([dict(ROW,连板数=n)]),d,'up')
    app.dependency_overrides[get_db]=lambda:db
    try:
        with TestClient(app) as api:
            result=api.get('/api/seasonality?start_year=2023&end_year=2024&height=2').json()['data']
            december=next(r for r in result if r['year']==2023 and r['month']==12)
            january=next(r for r in result if r['year']==2024 and r['month']==1)
            assert december['high_board_starts']==1
            assert january['high_board_starts']==0
            assert january['dragon_starts'] is None
    finally:app.dependency_overrides.clear()

def test_dashboard_calendar_gaps_are_null(db):
    calendar(db);ingest_pool(db,FixtureProvider(),DATE,'up');recalculate(db,DATE)
    app.dependency_overrides[get_db]=lambda:db
    try:
        with TestClient(app) as api:
            data=api.get('/api/dashboard').json()['data']
            by_date={r['trade_date']:r for r in data['history']}
            assert by_date['2024-01-03']['limit_up_count']==1
            assert by_date['2024-01-02']['limit_up_count'] is None
    finally:app.dependency_overrides.clear()


def test_repair_only_missing_partitions_and_skip_success(db):
    from app.collectors.repair import plan_pool_repairs, repair_pools
    calendar(db)
    ingest_pool(db,FixtureProvider(),DATE,'up')
    plan=plan_pool_repairs(db,DATE,DATE)
    assert plan==[{'trade_date':DATE,'kinds':['broken','down']}]
    class Recording(FixtureProvider):
        calls=[]
        def fetch(self,endpoint,**params):
            self.calls.append(endpoint)
            return super().fetch(endpoint,**params)
    client=Recording()
    results=repair_pools(db,DATE,DATE,client=client)
    assert results[0]['status']=='success'
    assert client.calls==['stock_zt_pool_zbgc_em','stock_zt_pool_dtgc_em']
    assert repair_pools(db,DATE,DATE,client=client)==[]


def test_repair_failure_stays_missing(db):
    from app.collectors.repair import repair_pools,plan_pool_repairs
    calendar(db)
    results=repair_pools(db,DATE,DATE,kinds=['up'],client=FixtureProvider(error='network failure'))
    assert results[0]['status']=='failed'
    assert plan_pool_repairs(db,DATE,DATE,kinds=['up'])==[{'trade_date':DATE,'kinds':['up']}]


def test_daily_reference_uses_same_response_and_checks_symbol():
    from app.providers.normalize import daily_ak
    from decimal import Decimal
    import pytest
    row={'日期':'2026-11-09','股票代码':'000001','开盘':10,'最高':11,'最低':10,'收盘':11,'成交量':100,'成交额':105000,'涨跌额':1,'涨跌幅':10}
    assert daily_ak(row,'000001.SZ')['pre_close']==Decimal('10')
    assert 'pre_close' not in daily_ak(dict(row,涨跌幅=5),'000001.SZ')
    with pytest.raises(ValueError,match='symbol'):
        daily_ak(row,'000002.SZ')
