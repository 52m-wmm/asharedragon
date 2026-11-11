from datetime import date
from typing import Literal
from fastapi import APIRouter,Query,HTTPException
from app.statistics.history_limit_queries import query,candles
from app.providers.normalize import stock_code
router=APIRouter(prefix='/api/history-limits',tags=['basic historical statistics'])

@router.get('')
def statistics(year:int=Query(2026,ge=2017,le=2026),month:int|None=Query(None,ge=1,le=12),trade_date:date|None=None,
    mode:Literal['all','confirmed']='all',height:int=Query(1,ge=1,le=100),view:Literal['daily','cycles']='daily',
    page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=100),classification:Literal['all','exclude_ipo','turnover']='all',exclude_st:bool=False,exclude_ipo:bool=False):
 try:return query(year,month,trade_date,mode,height,view,page,page_size,classification,exclude_st=exclude_st,exclude_ipo=exclude_ipo)
 except FileNotFoundError as exc:raise HTTPException(503,'请先运行本地历史涨停统计脚本') from exc

@router.get('/candles')
def historical_candles(code:str,trade_date:date):return candles(stock_code(code),trade_date)

@router.get('/non-ipo-study')
def non_ipo_study(year:int|None=Query(None,ge=2017,le=2026),month:int|None=Query(None,ge=1,le=12),height:int=Query(5,ge=3,le=100),
    cohort:Literal['all','confirmed','candidate']='all',page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=100),exclude_st:bool=False,exclude_ipo:bool=False):
 from app.statistics.non_ipo_study import study
 try:return study(year,month,height,cohort,page,page_size,exclude_st=exclude_st,exclude_ipo=exclude_ipo)
 except FileNotFoundError as exc:raise HTTPException(503,'缺少匹配的周期分类快照') from exc

@router.get('/cycle-study')
def cycle_study(year:int|None=Query(None,ge=2017,le=2026),month:int|None=Query(None,ge=1,le=12),height:int=Query(5,ge=3,le=100),
    cohort:Literal['all','confirmed','candidate']='all',page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=100),exclude_st:bool=False,exclude_ipo:bool=False):
 from app.statistics.non_ipo_study import study
 try:return study(year,month,height,cohort,page,page_size,exclude_st=exclude_st,exclude_ipo=exclude_ipo,non_ipo_only=False)
 except FileNotFoundError as exc:raise HTTPException(503,'缺少匹配的周期分类快照') from exc

@router.get('/sentiment/dates')
def sentiment_dates():
 from app.statistics.market_sentiment import dates
 return dates()

@router.get('/sentiment')
def market_sentiment(trade_date:date|None=None,start:date|None=None,end:date|None=None,exclude_st:bool=False,exclude_ipo:bool=False):
 from app.statistics.market_sentiment import query
 return query(trade_date,start,end,exclude_st,exclude_ipo)

@router.get('/sentiment/stocks')
def sentiment_stocks(trade_date:date,kind:Literal['up','first','second','third_plus','broken','down','advancing','declining','flat','promotion_1to2','promotion_2to3']='up',exclude_st:bool=False,exclude_ipo:bool=False,page:int=Query(1,ge=1),page_size:int=Query(50,ge=1,le=200)):
 from app.statistics.market_sentiment import stocks
 return stocks(trade_date,kind,exclude_st,exclude_ipo,page,page_size)
