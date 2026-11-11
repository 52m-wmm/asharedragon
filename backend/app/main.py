import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api.routes import router
from app.core.config import settings

app=FastAPI(title='AShareDragon',version='0.7.0')
app.add_middleware(CORSMiddleware,allow_origins=settings.cors_origins,allow_methods=['GET','POST'],allow_headers=['Content-Type'])
app.include_router(router)
from app.api.history import router as history_router
app.include_router(history_router)
from app.api.research import router as research_router
app.include_router(research_router)
from app.api.us_signals import router as us_signals_router
app.include_router(us_signals_router)
from app.api.dragons import router as dragons_router
app.include_router(dragons_router)

def error(status,message): return JSONResponse(status_code=status,content={'data':None,'meta':{},'error':{'message':message}})
@app.exception_handler(ValueError)
async def value_error(request:Request,exc:ValueError):return error(422,str(exc))
@app.exception_handler(RequestValidationError)
async def validation_error(request:Request,exc:RequestValidationError):return error(422,str(exc))
@app.exception_handler(HTTPException)
async def http_error(request:Request,exc:HTTPException):return error(exc.status_code,str(exc.detail))
@app.exception_handler(Exception)
async def server_error(request:Request,exc:Exception):
    logging.getLogger(__name__).exception('API request failed')
    return error(500,'服务暂时不可用，请查看后端日志')

from app.api.history_limits import router as history_limits_router
app.include_router(history_limits_router)

from app.api.data_inventory import router as data_inventory_router
app.include_router(data_inventory_router)

from app.api.indicator_lab import router as indicator_lab_router
app.include_router(indicator_lab_router)
