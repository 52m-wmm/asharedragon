from datetime import date
from decimal import Decimal
from app.analytics.rules import limit_price
from app.statistics.history_limits import rates,price


def test_st_reform_and_minimum_tick():
    for day,rate in [(date(2026,7,3),.05),(date(2026,7,6),.10),(date(2026,11,6),.10)]:
        actual=limit_price('1.86',trade_date=day,board='main',is_st=True,listing_session=100,registration_ipo=False)
        assert actual==price('1.86',rate)
        assert rates('600537.SH',day,True)==[rate]
    assert limit_price('.01',trade_date=date(2026,11,6),board='main',is_st=True,listing_session=100,registration_ipo=False)==Decimal('.02')


def test_unknown_st_only_blocks_when_rate_depends_on_it():
    args=dict(board='main',is_st=None,listing_session=100,registration_ipo=False)
    assert limit_price('1.86',trade_date=date(2026,7,3),**args) is None
    assert limit_price('1.86',trade_date=date(2026,7,6),**args)==Decimal('2.05')
    assert limit_price('1.86',trade_date=date(2026,7,6),exceptional=True,**args) is None
