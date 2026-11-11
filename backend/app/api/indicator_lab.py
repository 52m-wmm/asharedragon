from datetime import date
from fastapi import APIRouter, HTTPException, Query
from app.statistics import indicator_lab
router=APIRouter(prefix='/api/indicator-lab',tags=['indicator-lab'])

@router.get('/catalog')
def catalog():
 try:return indicator_lab.catalog()
 except FileNotFoundError as exc:raise HTTPException(503,'Published snapshot unavailable') from exc

@router.get('/series')
def series(start:date,end:date,indicators:str=Query(min_length=1,max_length=500),exclude_st:bool=False,exclude_ipo:bool=False,qqq_mode:str='strict'):
 try:return indicator_lab.query(indicators.split(','),str(start),str(end),exclude_st,exclude_ipo,qqq_mode)
 except FileNotFoundError as exc:raise HTTPException(503,'Published snapshot unavailable') from exc
