"""Read-only independent cross-market research; no live data requests in API handlers."""
from datetime import date
import json
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Query
from app.research.us_signals import USSignals

router=APIRouter(prefix='/api/research/us',tags=['cross-market research'])


def dataset():
    try:service=USSignals()
    except (FileNotFoundError,ValueError) as exc:
        raise HTTPException(503,'Build the independent US research snapshot first') from exc
    try:yield service
    finally:service.close()


@router.get('/qqq')
def qqq(start:date=date(2017,1,1),end:date=date(2026,11,6),page:int=Query(1,ge=1),page_size:int=Query(200,ge=1,le=1000),service:USSignals=Depends(dataset)):
    if start>end:raise ValueError('Invalid date range')
    rows=service.query('SELECT * FROM qqq WHERE us_trade_date BETWEEN ? AND ? ORDER BY us_trade_date LIMIT ? OFFSET ?',
        [start,end,page_size,(page-1)*page_size])
    count=service.query('SELECT count(*) AS n FROM qqq WHERE us_trade_date BETWEEN ? AND ?',[start,end])[0]['n']
    expected=service.query('SELECT count(*) AS n FROM us_calendar WHERE us_trade_date BETWEEN ? AND ?',[start,end])[0]['n']
    return {'data':rows,'meta':{'symbol':'QQQ','source':'nasdaq_public_historical','observed_rows':count,'scheduled_sessions':expected,
        'page':page,'page_size':page_size,'date_coverage':count/expected if expected else None,
        'calendar_bounds':['2016-12-01','2026-11-06'],'outside_calendar_range':start<date(2016,12,1) or end>date(2026,11,6),
        'price_basis':'provider OHLC, cash-unadjusted; no certified complete issuer split ledger',
        'return_basis':'price_return excludes dividends; cash_entitlement_return_proxy is reported ex-date cash accrual, not paid/reinvested net realized return',
        'use_as_preopen_signal':'Use association endpoint; raw historical rows do not establish historical availability'}}


@router.get('/association')
def association(start:date=date(2017,1,1),end:date=date(2026,11,6),mode:Literal['strict','retrospective']='strict',
                decision_time:str='09:30',page:int=Query(1,ge=1),page_size:int=Query(200,ge=1,le=1000),service:USSignals=Depends(dataset)):
    return service.joined(start,end,mode,decision_time,page,page_size)


@router.get('/experiments')
def experiments(service:USSignals=Depends(dataset)):
    path=service.root/service.manifest['dataset']/'experiments.json'
    if not path.exists():raise HTTPException(503,'Run the offline diagnostic evaluation first')
    return {'data':json.loads(path.read_text()),'meta':{'causal_claim':False,'profitability_claim':False,
        'pit_certified':False,'purpose':'descriptive and preliminary retrospective out-of-sample diagnostics only'}}


@router.get('/nq')
def nq():
    return {'data':None,'meta':{'coverage_status':'unavailable','preopen_price':None,
        'reason':'No verified free historical intraday source covering 2017-present',
        'daily_settlement_is_not_preopen_quote':True,'continuous_contract_rule_verified':False,
        'source_review':'docs/us_market_sources.md'}}
