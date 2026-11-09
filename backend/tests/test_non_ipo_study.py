import pytest
from app.statistics.history_limit_queries import ROOT,dataset
from app.statistics.non_ipo_study import study
pytestmark=pytest.mark.skipif(not (ROOT/'classification_manifest.json').exists(),reason='Local derived classification unavailable')

def test_yearly_conservation_and_evidence_partition():
 s=study()
 assert len(s['yearly'])==10
 assert sum(r['source_segments'] for r in s['yearly'])==117611
 for r in s['yearly']:
  assert r['source_segments']==r['non_ipo']+r['ipo_excluded']+r['ipo_unknown']
  assert r['non_ipo']==r['trusted_start']+r['start_unknown']
  assert r['high_samples']==r['high_trusted_start']+r['high_start_unknown']
  for h in [3,5,7,10]:assert r['ge'+str(h)]==r['confirmed_ge'+str(h)]+r['candidate_ge'+str(h)]
 assert s['quality']['non_ipo']==1740 and s['quality']['trusted_start']==1633
 assert sum(r['n'] for r in s['months'])==1633
 assert all(r['ipo_origin']=='non_ipo' for r in s['samples'])

def test_unknown_start_never_becomes_launch_month_and_empty_is_null():
 s=study(2017,height=10)
 assert s['total']==1 and s['samples'][0]['first_board_date'] is None
 assert all(m['n'] is None for m in s['months'])
 assert study(2017,month=11,height=10)['total']==0
 assert all(m['n'] is None for m in study(2017,cohort='confirmed')['months'])

def test_confirmed_cases_launch_dates_and_month_drilldown():
 for code,y,m,start,h in [('603178.SH',2023,9,'2023-09-28',14),('603580.SH',2024,3,'2024-03-05',13)]:
  s=study(y,m,10,'confirmed')
  r=next(r for r in s['samples'] if r['stock_code']==code)
  assert str(r['first_board_date'])==start and r['final_height']==h and r['status']=='confirmed'
  assert all(r['first_board_date'].month==m for r in s['samples'])
  assert s['months'][m-1]['confirmed']==s['total']
  assert all(r['candidate']==0 for r in s['months'])
