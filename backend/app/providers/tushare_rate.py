"""Persistent rolling window shared by all local proxy clients and processes."""
import fcntl
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

class RateStopped(RuntimeError):pass

class GlobalLimiter:
    def __init__(self,directory,clock=time.time,sleep=time.sleep):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.clock,self.sleep=clock,sleep

    def perform(self,call,endpoint,params,interval=2):
        with (self.directory/'scheduler.lock').open('a') as lock:
            # Lock includes the whole HTTP request: other processes cannot overlap.
            fcntl.flock(lock,fcntl.LOCK_EX)
            path=self.directory/'scheduler.json'
            state=json.loads(path.read_text()) if path.exists() else {}
            if state.get('consecutive_ip_limits',0)>=3:
                raise RateStopped('three consecutive IP limits; operator review required')
            now=self.clock();recent=[t for t in state.get('starts',[]) if now-t<60]
            due=max(state.get('cooldown_until',0),state.get('last_start',0)+max(2,interval))
            if len(recent)>=30:due=max(due,recent[-30]+60.01)
            wait=max(0,due-now)
            if wait:
                if wait>=10:print(json.dumps({'event':'global_wait','seconds':round(wait,2),'endpoint':endpoint}),flush=True)
                self.sleep(wait)
            started=self.clock()
            state['starts']=[t for t in recent if started-t<60]+[started]
            state['last_start']=started;state['total_requests']=state.get('total_requests',0)+1
            def save():
                tmp=path.with_suffix('.tmp')
                with tmp.open('w') as f:json.dump(state,f);f.flush();os.fsync(f.fileno())
                tmp.replace(path)
            def log(value):
                with (self.directory/'requests.jsonl').open('a') as f:
                    f.write(json.dumps(value,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
            record={'sequence':state['total_requests'],'pid':os.getpid(),'endpoint':endpoint,'params':params,
                    'started_at':datetime.fromtimestamp(started,timezone.utc).isoformat()}
            save();log({**record,'event':'request_start'})
            status='network';http=None
            try:
                response=call();http=response.status_code
                try:body=response.json()
                except ValueError:body={}
                msg=str(body.get('msg',body.get('message',''))) if isinstance(body,dict) else ''
                ip_limited='ip_rate_limited' in msg.lower() or http==429
                status='ip_rate_limited' if ip_limited else 'response'
                if ip_limited:
                    state['ip_limit_events']=state.get('ip_limit_events',0)+1
                    state['consecutive_ip_limits']=state.get('consecutive_ip_limits',0)+1
                    state['cooldown_until']=self.clock()+90
                else:
                    state['consecutive_ip_limits']=0
                return response
            finally:
                save();log({**record,'event':'request_end','finished_at':datetime.fromtimestamp(self.clock(),timezone.utc).isoformat(),
                            'http':http,'status':status,'consecutive_ip_limits':state.get('consecutive_ip_limits',0)})
