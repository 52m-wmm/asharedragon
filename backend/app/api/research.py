"""Additive research endpoints. Every response includes explicit coverage metadata."""
from datetime import date
from fastapi import APIRouter, Depends, Query, HTTPException
from app.providers.normalize import stock_code
from app.research.queries import Research

router=APIRouter(prefix='/api/research',tags=['research'])


def research():
    try:r=Research()
    except (ValueError,FileNotFoundError) as exc:raise HTTPException(503,'Research catalogue unavailable; run the local coverage audit') from exc
    try:yield r
    finally:r.close()


@router.get('/daily')
def daily(code:str,start:date,end:date,r:Research=Depends(research)):
    return r.stock(stock_code(code),start,end)


@router.get('/market')
def market(trade_date:date,page:int=Query(1,ge=1),page_size:int=Query(200,ge=1,le=1000),r:Research=Depends(research)):
    return r.market(trade_date,page,page_size)


@router.get('/preboard')
def preboard(code:str,event_date:date,r:Research=Depends(research)):
    return r.preboard(stock_code(code),event_date)


@router.get('/coverage')
def coverage(start:date=date(2017,1,1),end:date|None=None,r:Research=Depends(research)):
    return r.monthly(start,end or date.fromisoformat(r.catalog['end']))


@router.get('/events')
def events(start:date,end:date,code:str|None=None,page:int=Query(1,ge=1),page_size:int=Query(200,ge=1,le=1000),r:Research=Depends(research)):
    return r.events(start,end,stock_code(code) if code else None,page,page_size)


def unified_research():
    from app.research.unified import UnifiedResearch
    try:
        r = UnifiedResearch()
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(503, 'Unified release unavailable; run scripts/finalize_research.py') from exc
    try:
        yield r
    finally:
        r.close()


@router.get('/unified/daily')
def unified_daily(year:int=Query(...,ge=2017,le=2026),month:int|None=Query(None,ge=1,le=12),
                  code:str|None=None,page:int=Query(1,ge=1),page_size:int=Query(200,ge=1,le=1000),
                  r=Depends(unified_research)):
    return r.daily(year=year,month=month,code=stock_code(code) if code else None,page=page,page_size=page_size)


@router.get('/unified/summary')
def unified_summary(r=Depends(unified_research)):
    return r.summary
