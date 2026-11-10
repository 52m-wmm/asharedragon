from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
import pytest
from app.providers.normalize import stock_code, daily_ak, clock_time, number
from app.analytics.rules import limit_price, identify_limit
from app.analytics.metrics import sentiment, consecutive, returns
from app.services.calendar import completed_date

@pytest.mark.parametrize('source,expected',[('600000','600000.SH'),('sz000001','000001.SZ'),('300001.SZ','300001.SZ'),('688001','688001.SH'),('920001','920001.BJ'),('830001.BJ','830001.BJ')])
def test_codes(source,expected):assert stock_code(source)==expected
@pytest.mark.parametrize('source',['abc','000001.SH','600000.SZ','123456','123','sh000001'])
def test_bad_codes(source):
    with pytest.raises(ValueError):stock_code(source)

def price(board='main',is_st=False,day=date(2024,1,2),**kw):
    return limit_price('10.05',board=board,is_st=is_st,trade_date=day,listing_session=kw.pop('listing_session',100),registration_ipo=kw.pop('registration_ipo',False),**kw)
def test_limit_rules():
    assert price()==Decimal('11.06')
    assert price(is_st=True)==Decimal('10.55')
    assert price('chinext',day=date(2020,8,21))==Decimal('11.06')
    assert price('chinext',is_st=True,day=date(2020,8,24))==Decimal('12.06')
    assert price('star',is_st=True)==Decimal('12.06')
    assert price('bse')==Decimal('13.07')
    assert price('bse',listing_session=1) is None
    assert price('main',listing_session=5,registration_ipo=True) is None
    assert price('star',listing_session=3,registration_ipo=True) is None
    assert price(is_st=None) is None
    assert price(exceptional=True) is None
    assert price(is_st=True,day=date(2026,1,1)) == Decimal('10.55')
    assert price(official_price='11.07',exceptional=True)==Decimal('11.07')
    assert identify_limit('11.06','11.06','11.06')=='up'
    assert identify_limit('10.90','11.06','11.06')=='broken'
    assert identify_limit('11.05','11.05','11.06') is None
    assert identify_limit('11','11',None) is None

def e(code,height):return {'stock_code':code,'consecutive_limit_days':height}
def test_promotion_uses_same_cohort():
    result=sentiment([e('A',2),e('Z',2),e('C',3)],[{}],[{}],[e('A',1),e('B',1),e('C',2)])
    assert result['second_board_promotion_rate']==.5 # Z is not yesterday's first board
    assert result['third_board_promotion_rate']==1
    assert result['broken_limit_rate']==.25
    assert sentiment([],[],[],[])['second_board_promotion_rate'] is None
    assert sentiment([],[],[],[])['broken_limit_rate'] is None
    assert sentiment(None,[],[],[])['limit_up_count'] is None
    assert sentiment([e('A',None)],[],[],[])['first_board_count'] is None

def test_consecutive_gap():assert consecutive([True,True,None,True,False,True])==[1,2,None,None,0,1]
def test_future_horizon_and_drawdown():
    assert returns([10,11,12],0)['max_gain_5d'] is None
    result=returns([10,20,5]+[10]*18,0)
    assert result['max_gain_20d']==1
    assert result['max_drawdown_20d']==-.75
    assert result['past_return_5d'] is None

def test_exchange_close_cutoff():
    sh=ZoneInfo('Asia/Shanghai')
    assert completed_date(datetime(2026,11,4,15,30,tzinfo=sh))==date(2026,11,3)
    assert completed_date(datetime(2026,11,4,16,15,tzinfo=sh))==date(2026,11,4)

def test_nullable_units():
    row={'日期':'2024-01-02','开盘':10,'最高':11,'最低':9,'收盘':10.5,'成交量':100,'成交额':12345}
    values=daily_ak(row,'000001');assert values['volume']==10000;assert values['amount']==12345
    assert values['turnover_rate'] is None
    assert number('--') is None
    assert str(clock_time('93000'))=='09:30:00'
