from app.statistics.reference_recovery import recover_reference


def test_raw_change_restores_reference_and_checks_percent():
 assert recover_reference({'收盘':43.18,'涨跌额':.43,'涨跌幅':1.01})==42.75
 assert recover_reference({'收盘':11,'涨跌额':1,'涨跌幅':10})==10
 assert recover_reference({'收盘':11,'涨跌额':1,'涨跌幅':9}) is None
 assert recover_reference({'收盘':11,'涨跌幅':10}) is None
 assert recover_reference({'收盘':11,'涨跌额':.001,'涨跌幅':0}) is None


def test_local_recovery_result_and_unchanged_earlier_years():
 import json,pytest
 from app.statistics.history_limit_queries import ROOT,dataset,query
 if not (ROOT/'manifest.json').exists():pytest.skip('Local snapshot unavailable')
 with dataset() as (db,meta):
  if meta['version']!='historical-limit-statistics-v2':pytest.skip('v2 snapshot unavailable')
  assert meta['reference_recovery']['joined_references']==826278
  r=meta['years'][-1]
  assert (r['unknown'],r['confirmed'],r['reconstructed_candidate'])==(3785,623,15690)
  old=json.loads((ROOT/'snapshots/8daf351a03f8f8b43e94/summary.json').read_text())
  for y in meta['years'][:-1]+old['years'][:-1]:y['unknown_reasons'].sort(key=lambda x:x['reason'])
  assert meta['years'][:-1]==old['years'][:-1]
  assert db.execute('SELECT count(*) FROM cycles WHERE final_height IS NOT NULL AND (first_board_date IS NULL OR NOT end_complete)').fetchone()[0]==0
  assert db.execute("SELECT count(*) FROM cycles WHERE st_status='unknown'").fetchone()[0]>0
  for h in [3,5,7,10]:
   q=query(2026,view='cycles',height=h)
   assert q['total']==sum(c['ge'+str(h)] for c in r['cycles'])
   assert all(x['height']>=h for x in q['samples'])
   assert [x['height'] for x in q['samples']]==sorted([x['height'] for x in q['samples']],reverse=True)
