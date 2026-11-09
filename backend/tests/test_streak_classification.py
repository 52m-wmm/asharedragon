from app.statistics.streak_classification import classify_origin,morphology


def test_historical_ipo_anchors_and_no_limit_days():
 assert classify_origin(100,101,False,False)==('ipo_initial','first_regular_limit_session')
 assert classify_origin(100,100,False,False,True)[0]=='ipo_initial'
 assert classify_origin(100,100,False,False)[0]=='unknown'
 for start in range(100,105):assert classify_origin(100,start,True,False)[0]=='unknown'
 assert classify_origin(100,105,True,False)[0]=='ipo_initial'
 assert classify_origin(100,106,True,False)[0]=='unknown'
 assert classify_origin(100,106,True,True)[0]=='non_ipo'
 assert classify_origin(None,106,True,True)[0]=='unknown'


def test_ohlc_shape_does_not_infer_tradability_or_fill_missing():
 assert morphology(5,5,5,0,5)==('one_price_all',False)
 assert morphology(5,5,0,5,5)==('traded_range_all',True)
 assert morphology(5,5,2,3,5)==('mixed',False)
 assert morphology(5,4,0,4,5)==('unknown',False)
 assert morphology(5,5,0,5,4)==('traded_range_all',False)


def test_local_classification_dedup_counts_and_filters():
 import json,pytest
 from app.statistics.history_limit_queries import ROOT,dataset,query
 if not (ROOT/'classification_manifest.json').exists():pytest.skip('Local classification not installed')
 with dataset() as (db,meta):
  if 'classification' not in meta:pytest.skip('Classification snapshot mismatch')
  c=meta['classification'];assert c['duplicate_keys']==0 and c['overlapping_cycles']==0
  assert c['cycle_count']==c['unique_cycle_count']==117611
  for y in meta['years']:
   for h in [3,5,7,10]:
    assert sum(r['ge'+str(h)] for r in c['yearly'] if r['year']==y['year'])==sum(r['ge'+str(h)] for r in y['cycles'])
  assert db.execute('SELECT count(*)-count(distinct(stock_code,run_start)) FROM cycle_classes').fetchone()[0]==0
  assert db.execute("SELECT count(*) FROM cycle_classes WHERE turnover_candidate AND (ipo_origin<>'non_ipo' OR ohlc_shape<>'traded_range_all')").fetchone()[0]==0
 assert query(2017,view='cycles',height=10)['total']==183
 q=query(2017,view='cycles',height=10,classification='exclude_ipo')
 assert q['total']==4
 assert sum(r['ipo_origin']=='unknown' for r in q['samples'])==3
 assert query(2017,view='cycles',height=10,classification='turnover')['total']==0
 q=query(2024,month=3,view='cycles',height=10,mode='confirmed',classification='turnover')
 assert all(r['ipo_origin']=='non_ipo' and r['turnover_candidate'] and r['status']=='confirmed' for r in q['samples'])
