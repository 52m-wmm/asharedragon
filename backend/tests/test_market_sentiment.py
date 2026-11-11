"""Acceptance of E4 using installed immutable releases (no mocks)."""
import pytest
from app.statistics.market_sentiment import query,stocks,dates,active_signature

@pytest.mark.parametrize('st,ipo',[(False,False),(False,True),(True,False),(True,True)])
def test_scoped_cards_lists_and_queue(st,ipo):
 r=query(trade_date='2026-11-10',exclude_st=st,exclude_ipo=ipo)['data'][0]
 for kind,metric in [('up',r['limits']['up']),('first',r['ladder']['first']),('second',r['ladder']['second']),('third_plus',r['ladder']['third_plus']),('advancing',r['breadth']['up']),('broken',r['limits']['broken'])]:
  listing=stocks('2026-11-10',kind,st,ipo)
  assert listing['meta']['total']==(metric['value'] or 0)
 for q in r['promotions'].values():
  assert q['strict_value'] is None
  if q['value'] is not None:assert q['value']==q['numerator']/q['denominator']
 if st:assert r['breadth']['up']['value'] is None
 else:assert r['limits']['up']['value']==95

def test_accepted_d1_and_unknown_not_zero():
 r=query(trade_date='2026-11-10')['data'][0]
 assert r['d1']['common_count']==5216
 assert r['d1']['delta']==pytest.approx(37.576687116564415)
 assert r['d1']['direction']=='positive'
 assert r['limits']['down']['value'] is None
 assert r['limits']['down']['quality']=='unknown'
 assert r['limits']['confirmed']['value']==93 and r['limits']['candidate']['value']==2
 assert r['limits']['broken']['value']==19
 assert r['limits']['broken']['quality']=='supplier_diagnostic'
 assert r['promotions']['promotion_1to2']['denominator']==45
 assert r['promotions']['promotion_2to3']['denominator']==8

def test_calendar_and_validation():
 assert len(dates()['data'])==2391
 with pytest.raises(ValueError):query(trade_date='2026-11-08')
 with pytest.raises(ValueError):query(start='2017-01-03',end='2026-11-10',exclude_st=True)
 with pytest.raises(ValueError):query(trade_date='2026-11-10',start='2026-11-09')

def test_full_history_default_and_provenance():
 before=active_signature()
 result=query(start='2017-01-03',end='2026-11-10')
 assert len(result['data'])==2391
 assert len({r['trade_date'] for r in result['data']})==2391
 for r in result['data']:
  assert r['breadth_quality']['coverage_ratio'] is None
  for section in ('breadth','limits','ladder','promotions'):
   for m in r[section].values():
    assert m['source'] and m['snapshot'] and m['rule_version']
    assert m['quality'] in ('confirmed','candidate','supplier_diagnostic','unknown')
    if m['value'] is None:assert m['quality']=='unknown'
 assert before==active_signature()

@pytest.mark.parametrize('day',['2017-01-03','2020-08-24','2023-04-10','2026-03-13','2026-03-16','2026-11-09'])
def test_history_and_source_boundaries(day):
 r=query(trade_date=day,exclude_ipo=True)['data'][0]
 assert stocks(day,'up',exclude_ipo=True)['meta']['total']==(r['limits']['up']['value'] or 0)
 assert r['trade_date']==day

@pytest.mark.parametrize('st,ipo',[(False,False),(False,True),(True,False),(True,True)])
def test_promotion_lists_are_the_fixed_denominator(st,ipo):
 r=query(trade_date='2026-11-10',exclude_st=st,exclude_ipo=ipo)['data'][0]
 for kind in ('promotion_1to2','promotion_2to3'):
  listing=stocks('2026-11-10',kind,st,ipo,page_size=200)
  q=r['promotions'][kind]
  assert listing['meta']['total']==q['denominator']
  assert len(listing['data'])==q['denominator']
  assert sum(x['matched'] is True for x in listing['data'])==q['numerator']

def test_confirmed_zero_and_candidate_are_distinct():
 # First day has no trusted prior reference: unknown, not a confirmed zero.
 assert query(trade_date='2017-01-03')['data'][0]['limits']['up']['value'] is None
 r=query(trade_date='2017-01-04')['data'][0]
 assert r['limits']['confirmed']['value']==0
 assert r['limits']['confirmed']['quality']=='confirmed'
 assert r['limits']['candidate']['value']>0
 assert r['limits']['broken']['value'] is None

def test_http_contract_and_legacy_api():
 from fastapi.testclient import TestClient
 from app.main import app
 c=TestClient(app)
 assert c.get('/api/history-limits/sentiment/dates').json()['data'][-1]=='2026-11-10'
 assert c.get('/api/history-limits/sentiment?trade_date=2026-11-08').status_code==422
 assert c.get('/api/history-limits/sentiment/stocks?trade_date=2026-11-10&kind=promotion_2to3').json()['meta']['total']==8
 assert c.get('/api/history-limits/non-ipo-study?year=2026').status_code==200
