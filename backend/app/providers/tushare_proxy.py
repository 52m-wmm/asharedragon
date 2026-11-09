"""Explicit third-party transport, append-only local evidence, bounded requests.

No SDK default URL, no redirects, no implicit credentials, no database writes.
"""
import hashlib
import json
import time
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
import requests
from app.providers.tushare_rate import GlobalLimiter, RateStopped
from app.core.config import settings, ROOT

PROVIDER = 'tushare_proxy'
CONFIG = {
    'trade_cal': dict(cap=10000, timeout=30, interval=1.5, retries=1),
    'limit_list_d': dict(cap=2500, timeout=45, interval=1.5, retries=2),
    'kpl_list': dict(cap=8000, timeout=45, interval=2, retries=1),
    'limit_list_ths': dict(cap=4000, timeout=45, interval=2, retries=1),
    'daily_basic': dict(cap=6000, timeout=60, interval=2, retries=1),
    'stk_limit': dict(cap=5800, timeout=45, interval=2, retries=1),
    'stock_st': dict(cap=1000, timeout=30, interval=2, retries=1),
    'limit_cpt_list': dict(cap=2000, timeout=30, interval=2, retries=1),
}

class ProxyError(RuntimeError):
    def __init__(self, kind, message, attempts=None):
        self.kind, self.attempts = kind, attempts or []
        super().__init__(f'{kind}: {message}')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def parse(payload):
    data=payload.get('data',payload.get('result')) if isinstance(payload,dict) else payload
    if isinstance(data,dict) and 'fields' in data and 'items' in data:
        fields,items=data['fields'],data['items']
        if not isinstance(fields,list) or not all(isinstance(f,str) for f in fields) or len(fields)!=len(set(fields)) or not isinstance(items,list):
            raise ProxyError('schema','invalid fields/items')
        if any(not isinstance(r,list) or len(r)!=len(fields) for r in items):raise ProxyError('schema','row width mismatch')
        return fields,[dict(zip(fields,r)) for r in items]
    if isinstance(data,dict):data=next((data[k] for k in ('records','rows','list') if k in data),None)
    if not isinstance(data,list) or any(not isinstance(r,dict) for r in data):raise ProxyError('schema','unrecognized response')
    return list(dict.fromkeys(k for r in data for k in r)),data


class ProxyClient:
    name=PROVIDER
    def __init__(self,url=None,token=None,accept_http=False,archive=None,overrides=None,max_requests=100):
        self.url=url or settings.tushare_api_url
        self.token=token or settings.tushare_token
        u=urlsplit(self.url)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ProxyError('configuration','explicit clean TUSHARE_API_URL required')
        if u.hostname=='tushare.pro' or u.hostname.endswith('.tushare.pro'):
            raise ProxyError('configuration','proxy credentials cannot be sent to official Tushare hosts')
        if u.scheme=='http' and not accept_http:raise ProxyError('configuration','HTTP requires explicit accepted risk')
        if not self.token:raise ProxyError('configuration','TUSHARE_TOKEN missing')
        self.source_id=digest(self.url)[:16]
        self.archive=Path(archive or ROOT/'data/tushare_proxy')/self.source_id
        self.archive.mkdir(parents=True,exist_ok=True)
        self.config={k:{**v,**(overrides or {}).get(k,{})} for k,v in CONFIG.items()}
        self.session=requests.Session();self.session.trust_env=False
        self.used=0;self.max_requests=max_requests;self.last_request=0
        self.limiter=GlobalLimiter(Path(archive)/"scheduler" if archive else ROOT/"data/tushare_scheduler",sleep=lambda seconds: time.sleep(seconds))

    def safe(self,value):
        if isinstance(value,dict):return {k:('[REDACTED]' if any(s in k.lower() for s in ('token','password','secret','authorization')) else self.safe(v)) for k,v in value.items()}
        if isinstance(value,list):return [self.safe(v) for v in value]
        return value.replace(self.token,'[REDACTED]') if isinstance(value,str) else value

    def save(self,endpoint,params,payload):
        directory=self.archive/endpoint;directory.mkdir(exist_ok=True)
        path=directory/(digest(payload)+'.json')
        if not path.exists():
            temp=path.with_suffix('.tmp');temp.write_text(json.dumps(self.safe(payload),ensure_ascii=False,indent=2));temp.replace(path)
        return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)

    def fetch(self,endpoint,refresh=False,**params):
        if endpoint not in self.config:raise ProxyError('configuration','unsupported endpoint')
        cfg=self.config[endpoint];key=digest({'endpoint':endpoint,'params':params})
        manifest=self.archive/(key+'.json')
        if manifest.exists() and not refresh:
            path=Path(json.loads(manifest.read_text())['path'])
            if not path.is_absolute():path=ROOT/path
            stored=json.loads(path.read_text())
            if path.stem!=digest(stored):raise ProxyError('cache','archive content hash mismatch')
            if stored['source_id']!=self.source_id or stored['params']!=params:raise ProxyError('cache','manifest mismatch')
            stored['archive_path']=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
            return stored
        attempts=[]
        for attempt in range(max(2,cfg['retries'])+1):
            if self.used>=self.max_requests:raise ProxyError('budget','request cap reached',attempts)
            time.sleep(max(0,cfg['interval']-(time.monotonic()-self.last_request)))
            start=time.monotonic();self.last_request=start
            http=code=None;kind=None;message=None;raw=None
            try:
                def send():
                    self.used+=1
                    return self.session.post(self.url,json={'api_name':endpoint,'token':self.token,'params':params,'fields':''},
                                           timeout=(10,cfg['timeout']),allow_redirects=False,verify=True)
                response=self.limiter.perform(send,endpoint,params,cfg['interval'])
                http=response.status_code
                if 300<=http<400:raise ProxyError('redirect','credentials not forwarded')
                try:raw=response.json()
                except ValueError:raw={}
                code=raw.get('code',raw.get('status_code')) if isinstance(raw,dict) else None
                message=self.safe(str(raw.get('msg',raw.get('message','')))[:700]) if isinstance(raw,dict) else ''
                if http==429 or any(s in message.lower() for s in ('频率','频次','每分钟','rate limit','rate_limited','too many')):kind='rate_limit'
                elif 'upstream_pool_exhausted' in message or '稍后重试' in message:kind='upstream_unavailable'
                elif http in (401,403) or str(code)=='2002' or any(s in message.lower() for s in ('权限','积分','invalid token','无效token')):kind='permission'
                elif http>=400 or code not in (None,0,'0',200,'200') or (isinstance(raw,dict) and raw.get('success') is False):kind='api_error'
                if kind:raise ProxyError(kind,message)
                raw=self.safe(raw)
                fields,records=parse(raw)
                attempts.append(dict(http=http,code=code,seconds=round(time.monotonic()-start,3),status='success'))
                payload=dict(source_id=self.source_id,source_url=self.url,provider=PROVIDER,endpoint=endpoint,params=params,
                             fetched_at=datetime.now(timezone.utc).isoformat(),columns=fields,records=records,
                             response=self.safe(raw),attempts=attempts)
                path=self.save(endpoint,params,payload)
                payload['archive_path']=path
                # archive_path is metadata; manifest points at the immutable full response.
                temp=manifest.with_suffix('.tmp');temp.write_text(json.dumps({'path':path}));temp.replace(manifest)
                return payload
            except RateStopped:
                raise ProxyError('rate_limit_stopped','three consecutive IP limits; review scheduler log',attempts)
            except requests.exceptions.RequestException as exc:
                kind='tls' if isinstance(exc,requests.exceptions.SSLError) else 'network'
                message=type(exc).__name__
            except ProxyError as exc:
                kind=exc.kind;message=self.safe(str(exc))
            attempts.append(dict(http=http,code=self.safe(code),seconds=round(time.monotonic()-start,3),status=kind,error=message))
            failure=dict(source_id=self.source_id,endpoint=endpoint,params=params,attempts=attempts,response=self.safe(raw),fetched_at=datetime.now(timezone.utc).isoformat())
            self.save(endpoint,params,failure)
            if (attempt>=cfg['retries'] and kind!='rate_limit') or attempt>=max(2,cfg['retries']) or kind in ('permission','schema','redirect','tls') or (kind=='api_error' and (http or 0)<500):break
            if kind=='rate_limit' and ('ip_rate_limited' in (message or '') or http==429):
                continue  # global scheduler enforces the 90-second single-probe cooldown
            hint=re.search(r'(?:等待|wait\s*)(\d+)\s*(?:秒|seconds?)', message or '', re.I)
            wait=(int(hint.group(1)) if hint else 60)*2**attempt if kind in ('rate_limit','upstream_unavailable') else 2**attempt
            print(json.dumps({'event':'proxy_retry','endpoint':endpoint,'kind':kind,'wait_seconds':wait}),flush=True)
            time.sleep(wait)
        raise ProxyError(kind,message,attempts)
