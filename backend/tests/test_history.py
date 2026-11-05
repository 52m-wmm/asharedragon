"""Synthetic fixtures only in the existing isolated test schema, never research storage."""
from datetime import date,timedelta
from decimal import Decimal
from types import SimpleNamespace
import pytest
from sqlalchemy import select,func
from fastapi.testclient import TestClient
from app.providers.baostock import normalize_daily,from_vendor,to_vendor
from app.analytics.reconstruction import classify,streaks,compare
from app.collectors.history import basic_map,choose_sample,ingest_symbol,reconstruct,validate_run
from app.collectors.ingest import save_raw,new_job,ingest_pool
from app.models import Daily,TradingDay,ReconstructionDay,ReconstructionFact,ValidationReport
from app.main import app
from app.db.session import get_db

D=date(2024,1,3)

def row(**kw):
    return dict(stock_code='600000.SH',trade_date=D,pre_close=Decimal('10.05'),open=Decimal('10.05'),
                high=Decimal('11.06'),low=Decimal('10.05'),close=Decimal('11.06'),is_st=False,trade_status=True,**kw)

def rule_row(**kw):
    r=row();r.update(kw);return r

def classify_row(r,ipo=date(2000,1,4),out=None,previous=None):
    return classify(r,ipo,out,{ipo:0,r['trade_date']:100},previous)

def vendor(**kw):
    r=dict(date='2024-01-03',code='sh.600000',open='10.05',high='11.06',low='10.05',close='11.06',preclose='10.05',
           volume='100',amount='1106',adjustflag='3',turn='2.1',tradestatus='1',pctChg='10.0498',peTTM='8.2',pbMRQ='0.9',isST='0')
    r.update(kw);return r

def test_bao_units_and_historical_flags():
    r=normalize_daily(vendor());assert r['volume']==100;assert r['amount']==1106
    assert r['pe_ttm']==Decimal('8.2');assert r['pb']==Decimal('.9');assert r['is_st'] is False
    assert to_vendor('000001.SZ')=='sz.000001';assert from_vendor('sh.600000')=='600000.SH'
    assert normalize_daily(vendor(peTTM='',pbMRQ='',isST=''))['pe_ttm'] is None
    with pytest.raises(ValueError):normalize_daily(vendor(adjustflag='2'))
    with pytest.raises(ValueError):normalize_daily(vendor(isST='unknown'))
    with pytest.raises(ValueError):normalize_daily(vendor(high='9'))

def test_reconstruction_rules_and_st_effective_date():
    assert classify_row(row())['classification']=='up'
    assert classify_row(rule_row(close=Decimal('11.05')))['classification']=='not_up'
    assert classify_row(rule_row(is_st=True,high=Decimal('10.55'),close=Decimal('10.55')))['classification']=='up'
    for day,price in [(date(2026,7,3),'10.55'),(date(2026,7,6),'11.06')]:
        assert classify_row(rule_row(trade_date=day,is_st=True,high=Decimal(price),close=Decimal(price)))['classification']=='up'
    for code,day,price in [('300001.SZ',date(2020,8,21),'11.06'),('300001.SZ',date(2020,8,24),'12.06'),('688001.SH',D,'12.06')]:
        assert classify_row(rule_row(stock_code=code,trade_date=day,high=Decimal(price),close=Decimal(price)))['classification']=='up'
    assert classify_row(rule_row(stock_code='920001.BJ'))['classification']=='unknown'

def test_unknowns_are_not_false_negative_classifications():
    assert classify(None,None,None,{})['classification']=='missing'
    assert classify_row(rule_row(trade_status=False))['classification']=='suspended'
    assert classify_row(rule_row(is_st=None))['classification']=='unknown'
    assert classify_row(row(),ipo=None)['classification']=='unknown'
    assert classify_row(row(),out=D+timedelta(days=20))['reason']=='delisting_window_unverified'
    assert classify_row(row(),previous={'trade_status':False})['reason']=='resumption_special_rule_unverified'
    assert classify_row(rule_row(high=Decimal('12')))['classification']=='unknown'
    assert classify_row(rule_row(pre_close=Decimal('10.055')))['classification']=='unknown'

def test_ipo_sessions_and_no_limit():
    ipo=date(2024,1,2);r=rule_row(trade_date=date(2024,1,8))
    assert classify(r,ipo,None,{ipo:100,r['trade_date']:104})['classification']=='no_limit'
    assert classify(r,ipo,None,{ipo:100,r['trade_date']:105})['classification']=='up'
    legacy=date(2022,1,4);r=rule_row(trade_date=legacy)
    assert classify(r,legacy,None,{legacy:0})['classification']=='unknown'

def test_streaks_do_not_invent_start_across_gap():
    kinds=['up','not_up','up','up','missing','up','suspended','not_up','up']
    facts=[{'classification':k,'trade_date':D+timedelta(days=i)} for i,k in enumerate(kinds)]
    streaks(facts)
    assert [f['consecutive_limit_days'] for f in facts]==[None,None,1,2,None,None,None,None,1]
    assert facts[3]['first_board_date']==D+timedelta(days=2)
    assert facts[5]['first_board_date'] is None

def test_comparison_denominators_and_unassessable():
    facts=[{'stock_code':c,'classification':k,'reason':'test'} for c,k in [('A','up'),('B','up'),('C','not_up'),('D','not_up'),('E','unknown')]]
    r=compare(facts,['A','C','E','F'])
    assert (r['true_positive'],r['false_positive'],r['false_negative'],r['true_negative'])==(1,1,1,1)
    assert r['accuracy']==.5 and r['precision']==.5 and r['recall']==.5
    assert r['reference_coverage']==.5
    assert len(r['unassessable_reference'])==2
    assert compare([],[])['accuracy'] is None

def test_catalogue_includes_delisted_but_not_indices():
    raw=SimpleNamespace(raw_payload={'columns':['code','ipoDate','outDate','type','status'], 'records':[
        dict(code='sh.600091',code_name='退市明科',ipoDate='1997-07-04',outDate='2022-06-21',type='1',status='0'),
        dict(code='sz.399001',code_name='指数',ipoDate='1991-01-01',outDate='',type='2',status='1')]})
    values=basic_map(raw);assert list(values)==['600091.SH'];assert values['600091.SH']['delisting_date']==date(2022,6,21)
    values['689009.SH']={'delisting_date':None}
    values['000001.SZ']={'delisting_date':None}
    assert choose_sample(values,3)==choose_sample(values,3)
    assert set(choose_sample(values,3))==set(values)

class FakeBao:
    name='baostock'
    def __init__(self,records):self.records=records;self.calls=0
    def fetch(self,endpoint,**params):
        self.calls+=1;return {'columns':list(self.records[0]),'records':self.records,'version':'test-only'}

def setup_calendar(db):
    raw=save_raw(db,'baostock','test_calendar',None,{}, {'test_only':True},'success')
    for d in [date(2000,1,4),date(2024,1,2),D,date(2024,1,4)]:db.add(TradingDay(trade_date=d,exchange='SSE',source='test-only',raw_id=raw.id))
    db.commit()

def test_bao_ingestion_idempotent_and_source_isolated(db):
    setup_calendar(db);client=FakeBao([vendor()])
    assert ingest_symbol(db,'600000.SH',D,D,client)[:2]==(1,1)
    assert ingest_symbol(db,'600000.SH',D,D,client)[:2]==(1,0)
    assert client.calls==1
    record=db.scalar(select(Daily));assert record.provider=='baostock';assert record.available_at is None
    assert record.is_st is False

def test_reconstruction_storage_and_api_do_not_mix_observations(db):
    setup_calendar(db)
    client=FakeBao([vendor(date='2024-01-02',close='10.05',high='10.05'),vendor(),vendor(date='2024-01-04',preclose='11.06',open='11.06',low='11.06',close='12.17',high='12.17')])
    _,_,raw_id=ingest_symbol(db,'600000.SH',date(2024,1,2),date(2024,1,4),client)
    job=new_job(db,'history',D,date(2024,1,4),'baostock')
    universe={'600000.SH':{'listing_date':date(2000,1,4),'delisting_date':None},'600001.SH':{'listing_date':date(2000,1,4),'delisting_date':None}}
    run=reconstruct(db,job,['600000.SH'],universe,date(2024,1,2),{raw_id})
    facts=list(db.scalars(select(ReconstructionFact).order_by(ReconstructionFact.trade_date)))
    assert [f.consecutive_limit_days for f in facts]==[1,2]
    assert all(f.first_board_date==D for f in facts)
    days=list(db.scalars(select(ReconstructionDay).order_by(ReconstructionDay.trade_date)))
    assert days[0].coverage_rate==.5 and days[0].quality=='partial_sample_or_unclassified'
    assert days[1].second_board_promotion_rate==1
    app.dependency_overrides[get_db]=lambda:db
    try:
        with TestClient(app) as api:
            observed=api.get('/api/events?trade_date=2024-01-03').json();assert observed['data']==[]
            recon=api.get('/api/history/events?run_id='+run.run_id).json()
            assert recon['data'][0]['seal_amount'] is None
            assert recon['data'][0]['data_kind']=='algorithm_reconstructed'
            first=api.get('/api/history/first-boards?code=600000.SH').json();assert first['data'][0]['first_board_date']=='2024-01-03'
            summary=api.get('/api/history/summary').json();assert summary['data']['monthly'][0]['sample_limit_up_events']==2
            assert api.get('/api/history/validations').json()['data']==[]
    finally:app.dependency_overrides.clear()

def test_fullmarket_idempotency_and_conflicting_date(db):
    from app.collectors.history import ingest_market_day
    from app.models import Raw
    setup_calendar(db)
    client=FakeBao([vendor()])
    assert ingest_market_day(db,D,client)[:2]==(1,1)
    assert ingest_market_day(db,D,client)[:2]==(1,0)
    assert db.scalar(select(func.count()).select_from(Daily))==1
    with pytest.raises(ValueError,match='date/schema'):
        ingest_market_day(db,D,FakeBao([vendor(date='2024-01-04')]),force=True)
    assert db.scalar(select(func.count()).select_from(Daily))==1


def test_validation_persists_reference_and_errors(db):
    setup_calendar(db)
    client=FakeBao([vendor(date='2024-01-02',close='10.05',high='10.05'),vendor()])
    _,_,raw_id=ingest_symbol(db,'600000.SH',date(2024,1,2),D,client)
    job=new_job(db,'history',D,D,'baostock')
    universe={'600000.SH':{'listing_date':date(2000,1,4),'delisting_date':None}}
    run=reconstruct(db,job,['600000.SH'],universe,date(2024,1,2),{raw_id})
    observed=SimpleNamespace(name='akshare',fetch=lambda endpoint,**params: {'columns':['代码','名称'],'records':[{'代码':'600000','名称':'test-only','连板数':1}]})
    ingest_pool(db,observed,D,'up')
    validate_run(db,run);validate_run(db,run)
    reports=list(db.scalars(select(ValidationReport)))
    assert len(reports)==1 and reports[0].reference_raw_id is not None
    assert reports[0].result['precision']==1
    assert reports[0].result['recall']==1

def test_entirely_missing_history_day_is_not_zero(db):
    setup_calendar(db)
    job=new_job(db,'history',D,D,'baostock')
    run=reconstruct(db,job,['600000.SH'],{'600000.SH':{'listing_date':date(2000,1,4),'delisting_date':None}},date(2024,1,2),set())
    result=db.get(ReconstructionDay,(run.run_id,D))
    assert result.limit_up_count is None
    assert result.max_consecutive_boards is None
    assert result.coverage_rate==0
