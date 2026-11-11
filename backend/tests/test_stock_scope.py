from datetime import date,timedelta
import json
from pathlib import Path
import duckdb
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.statistics.stock_scope import ipo_phase,progression,CHOICE
from app.statistics.history_limit_queries import query,dataset
from app.statistics.non_ipo_study import study

@pytest.mark.parametrize('code,listed,cutoff',[
 ('600001.SH',date(2017,1,3),1),('300001.SZ',date(2020,8,21),1),
 ('300001.SZ',date(2020,8,24),5),('688001.SH',date(2019,7,22),5),
 ('600001.SH',date(2023,4,10),5),('600001.SH',date(2023,4,7),1)])
def test_dated_ipo_special_phase(code,listed,cutoff):
 # Calendar sessions deliberately include a weekend gap; no calendar-day shortcut.
 dates=[listed+timedelta(days=i) for i in range(15) if (listed+timedelta(days=i)).weekday()<5]
 cal={d:i for i,d in enumerate(dates)}
 for i,d in enumerate(dates):assert ipo_phase(code,listed,d,cal)==('special' if i<cutoff else 'regular')
 assert ipo_phase(code,None,dates[0],cal)=='unknown'
 assert ipo_phase(code,listed,listed-timedelta(days=1),cal)=='unknown'


def test_missing_calendar_or_unsupported_board_is_unknown():
 d=date(2026,1,1);assert ipo_phase('830001.BJ',d,d,{d:0})=='unknown'
 assert ipo_phase('600001.SH',d,d,{})=='unknown'


def test_promotion_matches_both_days_and_keeps_unknown_outcomes():
 db=duckdb.connect();db.execute("CREATE TABLE scope_calendar(trade_date DATE);INSERT INTO scope_calendar VALUES ('2026-01-30'),('2026-02-02')")
 db.execute('CREATE TABLE positives(stock_code VARCHAR,trade_date DATE,height INT,confirmed_height INT,status VARCHAR)')
 for code in ['a','b','c','d']:db.execute("INSERT INTO positives VALUES (?,'2026-01-30',1,1,'confirmed')",[code])
 db.execute("INSERT INTO positives VALUES ('a','2026-02-02',2,2,'confirmed')")
 db.execute('CREATE TABLE scope_members(stock_code VARCHAR,trade_date DATE,eligible BOOLEAN)')
 db.execute('CREATE TABLE original_progress_days(stock_code VARCHAR,trade_date DATE,up BOOLEAN,status VARCHAR,session_index INT)')
 for code,eligible,up in [('a',True,True),('b',True,False),('c',False,True),('d',True,None)]:
  db.execute("INSERT INTO scope_members VALUES (?,'2026-02-02',?)",[code,eligible]);db.execute("INSERT INTO original_progress_days VALUES (?,'2026-02-02',?,'confirmed',1)",[code,up])
 r=progression(db,date(2026,2,2))[0]
 assert (r['previous_cohort'],r['matched_cohort'],r['numerator'],r['denominator'],r['unknown'],r['excluded_current'],r['rate'])==(4,3,1,2,1,1,None)
 db.execute("UPDATE original_progress_days SET up=false WHERE stock_code='d'")
 assert progression(db,date(2026,2,2))[0]['rate']==pytest.approx(1/3)
 db.close()

@pytest.mark.parametrize('st,ipo',[(False,False),(True,False),(False,True),(True,True)])
@pytest.mark.parametrize('day',[date(2020,1,2),date(2026,11,10)])
def test_daily_ladder_counts_and_list_share_scope(st,ipo,day):
 r=query(day.year,day.month,day,exclude_st=st,exclude_ipo=ipo,page_size=10000)
 d=next(d for d in r['daily'] if d['trade_date']==day)
 assert sum(x['n'] for x in r['ladder'])==r['total']==len(r['samples'])
 assert (d['confirmed'] or 0)+(d['reconstructed_candidate'] or 0)==r['total']
 if st:assert all(x['st_status']=='confirmed_non_st' for x in r['samples'])
 for p in r['promotions']:
  assert p['numerator']<=p['denominator']<=p['matched_cohort']<=p['previous_cohort']
  if p['rate'] is not None:assert p['rate']==p['numerator']/p['denominator']

@pytest.mark.parametrize('st,ipo',[(False,False),(True,False),(False,True),(True,True)])
def test_cycle_study_tables_charts_lists_share_scope(st,ipo):
 r=study(2020,height=5,exclude_st=st,exclude_ipo=ipo,non_ipo_only=False,page_size=100000)
 assert len(r['samples'])==r['total']==r['quality']['non_ipo']
 assert sum(m['n'] or 0 for m in r['months'])==sum(x['first_board_date'] is not None for x in r['samples'])
 assert next(y['high_samples'] for y in r['yearly'] if y['year']==2020)==r['total']
 if st:assert all(x['st_status']=='confirmed_non_st' for x in r['samples'])


def test_new_h_legacy_non_ipo_api_with_pinned_classification(monkeypatch,tmp_path):
 root=Path.home()/'Datasets/asharedragon/statistics/history_limits'
 new=root/'classifications/eca9cb35fab48a985ade/manifest.json'
 assert new.exists()
 monkeypatch.setenv('ASHARE_HISTORY_DATASET','snapshots/95191cee081053e69364')
 monkeypatch.setenv('ASHARE_CLASSIFICATION_MANIFEST',str(new))
 c=TestClient(app);r=c.get('/api/history-limits/non-ipo-study?year=2026');assert r.status_code==200 and r.json()['total']>0
 assert r.json()['source']['snapshot'].endswith('/95191cee081053e69364')
 candles=c.get('/api/history-limits/candles?code=600537.SH&trade_date=2026-11-10')
 assert candles.status_code==200 and candles.json()['meta']['source_snapshot'].endswith('r1-20261111T012140556407Z-04ab0ef2')
 # Explicit old C proves the dependency gate after R2, independent of active.
 old=tmp_path/'old-classification-manifest.json'
 assert (root/'classifications/1545b023bafabb98a7c9/classifications.parquet').exists()
 old.write_text(json.dumps({'version':'streak-classification-v1','dataset':'classifications/1545b023bafabb98a7c9','source_dataset':'snapshots/e0860bd1d2439accd838'}))
 monkeypatch.setenv('ASHARE_CLASSIFICATION_MANIFEST',str(old))
 assert c.get('/api/history-limits/non-ipo-study?year=2026').status_code==503
 monkeypatch.setenv('ASHARE_HISTORY_DATASET','snapshots/e0860bd1d2439accd838')
 assert c.get('/api/history-limits/non-ipo-study?year=2026').status_code==200
 monkeypatch.delenv('ASHARE_CLASSIFICATION_MANIFEST')
 monkeypatch.delenv('ASHARE_HISTORY_DATASET')
 assert c.get('/api/history-limits/non-ipo-study?year=2026').status_code==200


def test_request_scope_does_not_leak_and_unknown_is_not_non_st():
 c=TestClient(app)
 r=c.get('/api/research/dragons/observed-streaks?exclude_st=true')
 assert r.status_code==200
 assert all(x['historical_st']['status']=='confirmed_non_st' for x in r.json()['data'])
 assert CHOICE.get()==(False,False)
 a=c.get('/api/research/dragons/observed-streaks').json()
 assert a['meta']['total']>=r.json()['meta']['total']
