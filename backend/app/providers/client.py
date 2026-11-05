import json
import logging
import subprocess
import sys
import time
from typing import Protocol
from app.core.config import settings

log = logging.getLogger(__name__)
class Provider(Protocol):
    name: str
    def fetch(self, endpoint: str, **params) -> dict: ...

class SourceError(RuntimeError): pass
class Client:
    def __init__(self, name='akshare', retries=None, interval=None, timeout=None):
        if name not in ('akshare','tushare','baostock'): raise ValueError('Unsupported provider')
        self.name = name
        self.retries = settings.request_retries if retries is None else retries
        self.interval = settings.request_interval if interval is None else interval
        self.timeout = settings.request_timeout if timeout is None else timeout
    def fetch(self, endpoint, **params):
        for attempt in range(self.retries + 1):
            time.sleep(self.interval * (attempt + 1))
            try:
                result = subprocess.run([sys.executable,'-m','app.providers.worker'],
                    input=json.dumps({'provider':self.name,'endpoint':endpoint,'params':params}),
                    text=True,capture_output=True,timeout=self.timeout)
                if result.returncode:
                    # Do not persist tracebacks or credentials from arbitrary vendor errors.
                    message = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else 'source process failed'
                    if settings.tushare_token: message = message.replace(settings.tushare_token,'[REDACTED]')
                    raise SourceError(message[:700])
                return json.loads(result.stdout)
            except (subprocess.TimeoutExpired, SourceError, json.JSONDecodeError) as exc:
                log.warning(json.dumps({'event':'source_retry','provider':self.name,'endpoint':endpoint,'attempt':attempt+1,'error':str(exc)[:700]}))
                if attempt == self.retries: raise SourceError(str(exc)[:700]) from exc

AK_ENDPOINTS = {'up':'stock_zt_pool_em','broken':'stock_zt_pool_zbgc_em','down':'stock_zt_pool_dtgc_em'}
TUSHARE_ENDPOINTS = ['limit_list_d','limit_list_ths','daily','daily_basic','stk_limit','stock_basic','trade_cal']
