from datetime import date
from typing import Literal
from fastapi import APIRouter,Query,HTTPException
from app.statistics.history_limit_queries import query,candles
from app.providers.normalize import stock_code
router=APIRouter(prefix='/api/history-limits',tags=['basic historical statistics'])

@router.get('')
def statistics(year:int=Query(2026,ge=2017,le=2026),month:int|None=Query(None,ge=1,le=12),trade_date:date|None=None,
    mode:Literal['all','confirmed']='all',height:int=Query(1,ge=1,le=100),view:Literal['daily','cycles']='daily',
    page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=100),classification:Literal['all','exclude_ipo','turnover']='all'):
 try:return query(year,month,trade_date,mode,height,view,page,page_size,classification)
 except FileNotFoundError as exc:raise HTTPException(503,'请先运行本地历史涨停统计脚本') from exc

@router.get('/candles')
def historical_candles(code:str,trade_date:date):return candles(stock_code(code),trade_date)

@router.get('/non-ipo-study')
def non_ipo_study(year:int|None=Query(None,ge=2017,le=2026),month:int|None=Query(None,ge=1,le=12),height:int=Query(5,ge=3,le=100),
    cohort:Literal['all','confirmed','candidate']='all',page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=100)):
 from app.statistics.non_ipo_study import study
 try:return study(year,month,height,cohort,page,page_size)
 except FileNotFoundError as exc:raise HTTPException(503,'缺少匹配的周期分类快照') from exc
