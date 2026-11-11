from datetime import date,datetime,timezone
import pytest
from app.statistics.indicator_lab import qqq_point,query,DEFS
from app.research.us_signals import us_schedule

@pytest.mark.parametrize('ashare,expected',[('2026-11-09','2026-11-06'),('2026-03-09','2026-03-06'),('2026-03-10','2026-03-09'),('2025-12-01','2025-11-28')])
def test_qqq_actual_prior_session(ashare,expected):
 schedule=us_schedule('2025-01-01','2026-11-11');d=date.fromisoformat(expected)
 bars={d:dict(close=123.0,return_quality='checked_close_pair',price_return=.01,cash_entitlement_return_proxy=.01,source_observed_at_utc=datetime(2026,11,11,tzinfo=timezone.utc))}
 value,quality,e=qqq_point(ashare,bars,schedule,'retrospective')
 assert value==123 and quality=='candidate' and e['us_trade_date']==d
 assert e['session_close_utc']<e['assumed_available_at_utc']<e['cutoff_utc']
 assert qqq_point(ashare,bars,schedule,'strict')[0] is None
 assert qqq_point('2026-11-10',bars,schedule,'retrospective')[0] is None


def test_real_projection_and_four_scopes():
 from app.statistics import market_sentiment as ms
 sig=ms.active_signature()
 for st,ipo in ((False,False),(True,False),(False,True),(True,True)):
  actual=query(list(DEFS),'2026-11-09','2026-11-10',st,ipo,'retrospective')
  old=ms.query(start='2026-11-09',end='2026-11-10',exclude_st=st,exclude_ipo=ipo)
  lookup={(p['trade_date'],p['indicator_id']):p for p in actual['data']}
  for row in old['data']:
   for key,group,field in [('breadth_up','breadth','up'),('breadth_down','breadth','down'),('advancing_ratio','breadth','advancing_ratio'),('limit_up','limits','up'),('limit_down','limits','down'),('broken_rate','limits','broken_rate')]+[(k,'ladder',k) for k in ('first','second','third_plus','max_height')]+[(k,'promotions',k) for k in ('promotion_1to2','promotion_2to3')]:
    p=lookup[row['trade_date'],key];expected=row[group][field]
    assert p['value']==expected['value'];assert p['quality']==expected['quality']
    assert p['snapshot_version']==expected['snapshot']
  from app.research.us_signals import USSignals
  with USSignals() as us:
   assert lookup['2026-11-09','qqq_close']['value']==us.bars[date(2026,11,6)]['close']
  assert lookup['2026-11-10','qqq_close']['value'] is None
 assert sig==ms.active_signature()


def test_validation_and_no_zero_imputation():
 with pytest.raises(ValueError):query(['nq'],'2026-11-09','2026-11-10')
 with pytest.raises(ValueError):query(['limit_up'],'2026-11-10','2026-11-09')
 with pytest.raises(ValueError):query(['qqq_close'],'2026-11-09','2026-11-10',qqq_mode='unsafe')
 data=query(['qqq_close','limit_down'],'2026-11-10','2026-11-10')['data']
 assert all(p['value'] is None and p['quality']=='unknown' for p in data)
