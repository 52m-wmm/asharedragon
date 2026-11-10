from datetime import date
from decimal import Decimal
import duckdb,pyarrow as pa
from app.statistics.history_limits import rates,price,classification_sql,streak_sql


def day(**kw):
 r=dict(reference_recovered=False,stock_code='600001.SH',stock_name='fixture',trade_date=date(2020,12,1),session_index=0,
   open=10.,high=11.,low=10.,close=11.,pre_close=10.,volume=100.,amount=1100.,listing_date=date(2000,1,4),delisting_date=None,listing_session=100,
   is_st=None,trade_status=True,price_quality='verified',is_limit_up=None,limit_quality='unknown',official_limit_up_price=None,
   event_conflict=False,price_conflict=False,status_conflict=False,event_source=None,consecutive_limit_days=None)
 return r|kw


def classify(items):
 with duckdb.connect() as db:
  db.register('input',pa.Table.from_pylist(items));c=db.execute(classification_sql());return [dict(zip([x[0] for x in c.description],r)) for r in c.fetchall()]


def test_historical_regimes_and_decimal_rounding():
 assert rates('300001.SZ',date(2020,8,21),True)==[.05]
 assert rates('300001.SZ',date(2020,8,24),True)==[.20]
 assert rates('688001.SH',date(2020,1,1),True)==[.20]
 assert rates('600001.SH',date(2026,7,3),True)==[.05]
 assert rates('600001.SH',date(2026,7,6),True)==[.10]
 assert price('10.05',.10)==Decimal('11.06')
 assert price('.01',.05)==Decimal('.02')


def test_st_unknown_candidates_do_not_become_non_st_or_confirmed():
 for close in (10.5,11.):
  r=classify([day(close=close,high=close)])[0]
  assert r['up'] and r['status']=='reconstructed_candidate' and r['st_status']=='unknown'
 assert not classify([day(is_st=True)])[0]['up'] # exceeds 5% => unknown, not a 10% ST label
 assert classify([day(is_st=True,high=10.5,close=10.5)])[0]['up']


def test_events_override_reference_missing_but_not_no_limit_ipo():
 r=classify([day(pre_close=None,is_limit_up=True,limit_quality='verified')])[0]
 assert r['up'] and r['status']=='confirmed'
 r=classify([day(listing_date=date(2020,12,1),listing_session=1,is_limit_up=True,limit_quality='verified')])[0]
 assert r['status']=='confirmed' # trusted event on old 44%-limit IPO is not discarded
 r=classify([day(stock_code='688001.SH',listing_date=date(2020,12,1),listing_session=1,is_limit_up=True,limit_quality='verified')])[0]
 assert r['status']=='unknown' and r['reason']=='ipo_no_limit_first_five'
 assert classify([day(pre_close=None)])[0]['up'] is None


def test_special_reference_and_calendar_gaps_remain_unknown():
 r=classify([day(close=10.,high=10.),day(trade_date=date(2020,12,2),session_index=1,pre_close=9.,high=9.9,close=9.9)])[-1]
 assert r['up'] is None and r['reason']=='ex_reference_unverified'
 r=classify([day(close=10.,high=10.),day(trade_date=date(2020,12,3),session_index=2)])[-1]
 assert r['up'] is None and r['reason']=='gap_or_resumption_special_unknown'


def test_streaks_never_bridge_unknown_or_missing_calendar_day():
 items=[]
 for index,up,status in [(0,False,'confirmed'),(1,True,'confirmed'),(2,True,'reconstructed_candidate'),(3,None,'unknown'),(4,True,'confirmed'),(6,True,'confirmed')]:
  items.append(dict(stock_code='600001.SH',trade_date=date(2020,12,index+1),session_index=index,up=up,status=status))
 with duckdb.connect() as db:
  db.register('days',pa.Table.from_pylist(items));c=db.execute(streak_sql());rr=[dict(zip([x[0] for x in c.description],r)) for r in c.fetchall()]
 rr=sorted(rr,key=lambda r:r['session_index'])
 assert [r['height'] for r in rr]==[1,2,1,1]
 assert rr[1]['chain_status']=='reconstructed_candidate'
 assert rr[0]['first_board_date']==date(2020,12,2)
 assert rr[2]['first_board_date'] is None and rr[3]['first_board_date'] is None
 assert rr[2]['confirmed_height']==1


def test_real_snapshot_benchmarks_and_coverage():
 import pytest
 from pathlib import Path
 from app.statistics.history_limit_queries import ROOT,dataset,query,candles
 if not (ROOT/'manifest.json').exists():pytest.skip('Local historical snapshot not installed')
 with dataset() as (db,meta):
  with duckdb.connect(str(Path(meta['source_snapshot'])/'research.duckdb'),read_only=True) as source:
   expected_rows=source.execute("SELECT count(*) FROM stock_daily WHERE price_quality='verified'").fetchone()[0]
  assert meta['daily_target_rows']==expected_rows
  assert meta['benchmark_2020_dec']==dict(archive_events=1039,confirmed_matches=1039,excluded_or_unknown=0)
  assert db.execute('SELECT count(*)-count(distinct(stock_code,trade_date)) FROM positives').fetchone()[0]==0
  assert all(r['reconstructed_candidate']>0 for r in meta['years'])
  assert sum(r['daily_rows'] for r in meta['years'])==expected_rows
  assert db.execute('SELECT count(*) FROM daily WHERE unknown<>unknown_observed+missing_catalogue_rows').fetchone()[0]==0
 for code,year,month,end,start,height in [('603178.SH',2023,10,'2023-10-25','2023-09-28',14),('603580.SH',2024,3,'2024-03-21','2024-03-05',13)]:
  result=query(year,month,date.fromisoformat(end),mode='confirmed',height=height)
  row=next(r for r in result['samples'] if r['stock_code']==code)
  assert row['confirmed_height']==height and str(row['confirmed_run_start'])==start
  assert row['status']=='confirmed'
  assert any(str(r['trade_date'])==end for r in candles(code,date.fromisoformat(end))['data'])
 assert query(2017,mode='confirmed')['total']==0
 assert query(2017,view='cycles',height=10)['total']>0


def test_recovered_reference_keeps_ex_right_and_resumption_unknown():
 r=classify([day(close=10.,high=10.),day(trade_date=date(2020,12,2),session_index=1,reference_recovered=True,pre_close=9.,high=9.9,close=9.9)])[-1]
 assert r['up'] is None and r['reason']=='ex_reference_unverified'
 assert r['reference_basis']=='raw_close_minus_change_pct_checked'
 r=classify([day(trade_status=False),day(trade_date=date(2020,12,2),session_index=1,reference_recovered=True)])[-1]
 assert r['up'] is None and r['reason']=='gap_or_resumption_special_unknown'


def test_cycle_end_requires_next_calendar_day_negative():
 items=[dict(stock_code='600001.SH',trade_date=date(2020,12,i+1),session_index=i,up=up,status='confirmed') for i,up in enumerate([False,True,True,False,True,None])]
 with duckdb.connect() as db:
  db.register('days',pa.Table.from_pylist(items));c=db.execute(streak_sql());rr=[dict(zip([x[0] for x in c.description],r)) for r in c.fetchall()]
 rr=sorted(rr,key=lambda r:r['trade_date'])
 assert rr[1]['exact_end'] is True
 assert rr[-1]['exact_end'] is False


def test_daily_shapes_and_touch_exclude_ambiguous_st():
 for fields,expected in [
  (dict(open=11.,low=11.),'one_price'),
  (dict(open=11.,low=10.8),'t_shape'),
  (dict(open=10.,low=10.),'other_close_up'),
  (dict(close=10.8),'touch_unsealed'),
  (dict(close=10.8,high=10.9),'not_limit_up'),
  (dict(open=None),'unknown'),
  (dict(high=12.),'unknown'),
 ]:
  result=classify([day(is_st=False,**fields)])[0]
  assert result['daily_shape']==expected
 assert classify([day(close=10.8)])[0]['daily_shape']!='touch_unsealed'
 assert classify([day(is_st=False,close=10.8,trade_status=False)])[0]['daily_shape']=='unknown'
 # A current ST name does not overwrite the historical daily status.
 result=classify([day(stock_name='ST样本',is_st=False)])[0]
 assert result['st_status']=='confirmed_non_st' and result['up']


def test_intraday_counts_deduplicate_sources_and_keep_missing_unknown():
 from app.statistics.history_limit_queries import intraday_counts
 with duckdb.connect() as db:
  db.execute("CREATE TABLE source_events(stock_code VARCHAR,limit_type VARCHAR,trade_date DATE)")
  db.execute("INSERT INTO source_events VALUES ('600001.SH','up','2026-11-09'),('600002.SH','broken','2026-11-09'),('600002.SH','broken','2026-11-09'),('600003.SH','down','2026-11-09')")
  db.execute("CREATE TABLE days(stock_code VARCHAR,trade_date DATE,up BOOLEAN,daily_shape VARCHAR,status VARCHAR)")
  db.execute("INSERT INTO days VALUES ('600001.SH','2026-11-09',true,'one_price','confirmed'),('600002.SH','2026-11-09',false,'touch_unsealed','confirmed'),('600004.SH','2026-11-09',true,'one_price','reconstructed_candidate')")
  assert intraday_counts(db,date(2026,11,9),'all')==dict(touched=3,broken=1,down=1)
  assert intraday_counts(db,date(2026,11,9),'confirmed')==dict(touched=2,broken=1,down=1)
  assert intraday_counts(db,date(2026,11,10),'all')==dict(touched=None,broken=None,down=None)
  from app.statistics.history_limit_queries import intraday_series
  db.execute("INSERT INTO source_events VALUES ('600005.SH','down','2026-11-10')")
  series=intraday_series(db,date(2026,11,9),date(2026,11,10),'all')
  assert series==[
   dict(trade_date=date(2026,11,9),touched=3,broken=1,down=1),
   dict(trade_date=date(2026,11,10),touched=None,broken=None,down=1),
  ]
