from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select, func
from app.models import TradingDay

SHANGHAI=ZoneInfo('Asia/Shanghai')
def completed_date(now=None):
    now=(now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    return now.date() if now.time()>=time(16,15) else now.date()-timedelta(days=1)

def sessions(db,start,end):
    if start>end: raise ValueError('开始日期不能晚于结束日期')
    low,high=db.execute(select(func.min(TradingDay.trade_date),func.max(TradingDay.trade_date))).one()
    if low is None or start<low or end>high: raise ValueError('交易日历未覆盖请求区间，请先同步日历')
    if end>completed_date(): raise ValueError('仅允许采集上海时间已完成的交易日（16:15 后）')
    return list(db.scalars(select(TradingDay.trade_date).where(TradingDay.trade_date.between(start,end),TradingDay.exchange=='SSE').order_by(TradingDay.trade_date)))

def previous_day(db,day):
    return db.scalar(select(func.max(TradingDay.trade_date)).where(TradingDay.trade_date<day,TradingDay.exchange=='SSE'))
