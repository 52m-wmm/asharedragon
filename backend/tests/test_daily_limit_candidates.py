from datetime import date
from decimal import Decimal
import duckdb
import pytest
from app.statistics.daily_limit_candidates import bounds,candidate_sql

@pytest.mark.parametrize('board,day,st,rates',[
 ('main','2024-01-02',True,['0.05']),('main','2025-06-30',None,['0.05','0.10']),
 ('main','2026-07-03',True,['0.05']),('main','2026-07-06',True,['0.10']),
 ('chinext','2024-01-02',True,['0.20']),('star','2024-01-02',None,['0.20'])])
def test_historical_rates(board,day,st,rates):
 assert [x[0] for x in bounds(10,board,date.fromisoformat(day),st)]==list(map(Decimal,rates))

def test_symmetric_tick_rounding_and_reference():
 assert bounds('10.05','main',date(2024,1,2),False)[0][1:]==(Decimal('11.06'),Decimal('9.05'))
 assert bounds('0.03','main',date(2024,1,2),True)[0][1:]==(Decimal('0.04'),Decimal('0.02'))
 for invalid in ('NaN',0,-1,'10.001'):
  with pytest.raises(ValueError):bounds(invalid,'main',date(2024,1,2),False)

def fixture_db():
 db=duckdb.connect();db.execute('CREATE SCHEMA src')
 db.execute("CREATE TABLE src.stocks AS SELECT '600001.SH' stock_code, DATE '2024-06-24' listing_date,NULL::DATE delisting_date")
 db.execute("CREATE TABLE src.stock_daily AS SELECT '600001.SH' stock_code,DATE '2024-06-28' trade_date,true trade_status")
 db.execute("CREATE TABLE listing_cal AS SELECT * FROM (VALUES(DATE '2024-06-24',DATE '2024-06-21',1),(DATE '2024-06-28',DATE '2024-06-27',5),(DATE '2024-07-01',DATE '2024-06-28',6)) t(trade_date,prev,idx)")
 db.execute("""CREATE TABLE evaluated AS SELECT '600001.SH' stock_code,DATE '2024-07-01' trade_date,'id' stock_day_id,
 10.00::DOUBLE open,10.50::DOUBLE high,10.00::DOUBLE low,10.50::DOUBLE close,10.00::DOUBLE reference_price,
 'same_response_reference_pct_checked' reference_basis,'purchased_test' price_source,1 pg_daily_id,1 supplement_raw_id,
 'valid' price_reason,'unknown' identity_status,'verified' lifecycle_status,true trade_status,false price_conflict,false event_conflict,false status_conflict""")
 db.execute("CREATE TABLE rate_options AS SELECT * FROM (VALUES(DATE '2024-07-01','main','unknown',.05),(DATE '2024-07-01','main','unknown',.10)) t(trade_date,board,identity_status,rate)")
 return db

def result(db):
 c=db.execute(candidate_sql());return dict(zip([x[0] for x in c.description],c.fetchone()))

def test_unknown_st_is_scenario_not_confirmed():
 with fixture_db() as db:
  r=result(db);assert r['up_candidate'] is True and r['up_all_scenarios'] is False
  assert r['scenario_count']==2 and r['candidate_reason']=='historical_st_unknown_5_or_10_scenarios'

def test_touch_is_only_candidate_and_no_sealed_inference():
 with fixture_db() as db:
  db.execute('UPDATE evaluated SET close=10.2');r=result(db)
  assert r['broken_candidate'] is True and not r['broken_all_scenarios'] and not r['up_candidate']

def test_reference_unknown_and_ipo_are_not_zero():
 with fixture_db() as db:
  db.execute("UPDATE evaluated SET price_reason='reference_unverified'");assert result(db)['up_candidate'] is None
  db.execute("UPDATE evaluated SET price_reason='valid',trade_date=DATE '2024-06-28'");r=result(db)
  assert r['candidate_reason']=='ipo_no_limit_first_five' and r['down_candidate'] is None

def test_excess_price_and_suspension_quarantine():
 with fixture_db() as db:
  db.execute('UPDATE evaluated SET high=12');r=result(db)
  assert r['candidate_reason']=='ohlc_outside_all_rule_scenarios' and r['up_candidate'] is None
  db.execute("UPDATE evaluated SET price_reason='suspended',trade_status=false");assert result(db)['broken_candidate'] is None

def test_event_labels_do_not_change_rule_benchmark():
 with fixture_db() as db:
  before=result(db);db.execute('UPDATE evaluated SET event_conflict=true');after=result(db)
  assert before['up_candidate']==after['up_candidate']

@pytest.fixture
def real_candidate():
 from pathlib import Path
 import os,json,hashlib
 if not os.environ.get('ASHARE_DAILY_LIMIT_CANDIDATE'):pytest.skip('Explicit offline daily limit candidate fixture not supplied')
 p=Path(os.environ['ASHARE_DAILY_LIMIT_CANDIDATE'])
 m=json.loads((p/'manifest.json').read_text())
 assert m['candidate_only']
 assert all(hashlib.sha256((p/f).read_bytes()).hexdigest()==h for f,h in m['files'].items())
 return p

def test_real_calendar_and_stock_keys(real_candidate):
 with duckdb.connect() as db:
  assert db.execute('SELECT count(*) FROM read_parquet(?)',[str(real_candidate/'daily.parquet')]).fetchall()[0][0]==690
  db.read_parquet(str(real_candidate/'20??-??.parquet')).create_view('facts')
  assert db.execute("SELECT count(*) FROM facts WHERE trade_date>=DATE '2024-01-01'").fetchall()[0][0]==3546140
  assert db.execute('SELECT count(*)-count(DISTINCT(stock_code,trade_date)) FROM facts').fetchall()[0][0]==0

def test_real_daily_aggregates_and_statuses(real_candidate):
 with duckdb.connect() as db:
  db.read_parquet(str(real_candidate/'20??-??.parquet')).create_view('facts')
  db.read_parquet(str(real_candidate/'daily.parquet')).create_view('daily')
  for k in ('up','down','broken'):
   assert db.execute(f"SELECT count(*) FROM facts WHERE ({k} IS NULL)<>({k}_status='unknown')").fetchall()[0][0]==0
   assert db.execute(f"SELECT count(*) FROM facts WHERE {k}_status='confirmed' AND event_{k} IS NOT TRUE").fetchall()[0][0]==0
   sql=f"""SELECT count(*) FROM (SELECT trade_date,count(*) FILTER(WHERE {k} AND {k}_status='confirmed') c,
    count(*) FILTER(WHERE {k} AND {k}_status='candidate') p,count(*) FILTER(WHERE {k} IS NULL) u
    FROM facts WHERE trade_date>=DATE '2024-01-01' GROUP BY 1) a JOIN daily d USING(trade_date)
    WHERE a.c<>d.{k}_confirmed OR a.p<>d.{k}_candidate OR a.u<>d.{k}_unknown"""
   assert db.execute(sql).fetchall()[0][0]==0
  assert db.execute("SELECT count(*) FROM facts WHERE candidate_reason='ipo_no_limit_first_five' AND (up IS NOT NULL OR down IS NOT NULL OR broken IS NOT NULL)").fetchall()[0][0]==0

def test_real_comparison_retains_disagreements(real_candidate):
 with duckdb.connect() as db:
  db.read_parquet(str(real_candidate/'event_comparisons.parquet')).create_view('c')
  assert db.execute("SELECT count(*) FROM c WHERE provider='akshare' AND comparison='conflict'").fetchall()[0][0]==7
  assert db.execute("SELECT count(*) FROM c WHERE provider='tushare_proxy' AND comparison='conflict'").fetchall()[0][0]==39
  assert db.execute("SELECT count(*) FROM c WHERE provider='tushare_proxy' AND period='target_period'").fetchall()[0][0]==0
