import json
from unittest.mock import Mock
import pytest
from app.providers.tushare_rate import GlobalLimiter,RateStopped

class Clock:
    value=1000
    def now(self):return self.value
    def sleep(self,seconds):self.value+=seconds

def response(limited=False):
    r=Mock(status_code=200);r.json.return_value={'code':-1 if limited else 0,'msg':'ip_rate_limited' if limited else ''};return r

def test_window_shared_between_clients_and_failures_count(tmp_path):
    clock=Clock();a=GlobalLimiter(tmp_path,clock.now,clock.sleep);b=GlobalLimiter(tmp_path,clock.now,clock.sleep)
    for n in range(32):
        (a if n%2 else b).perform(lambda:response(),'limit_list_d' if n%2 else 'daily_basic',{})
    logs=[json.loads(s) for s in (tmp_path/'requests.jsonl').read_text().splitlines() if json.loads(s)['event']=='request_start']
    from datetime import datetime
    times=[datetime.fromisoformat(s['started_at']).timestamp() for s in logs]
    assert min(y-x for x,y in zip(times,times[1:]))>=2
    assert all(sum(t<=v<t+60 for v in times)<=30 for t in times)
    def fail():raise TimeoutError()
    with pytest.raises(TimeoutError):a.perform(fail,'daily_basic',{})
    assert json.loads((tmp_path/'scheduler.json').read_text())['total_requests']==33

def test_three_ip_limits_cooldown_persists_restart(tmp_path):
    clock=Clock()
    for n in range(3):
        GlobalLimiter(tmp_path,clock.now,clock.sleep).perform(lambda:response(True),'limit_list_d',{})
        assert clock.value==1000+90*n
    with pytest.raises(RateStopped):GlobalLimiter(tmp_path,clock.now,clock.sleep).perform(lambda:response(),'daily_basic',{})
    assert json.loads((tmp_path/'scheduler.json').read_text())['total_requests']==3


def test_file_lock_covers_inflight_request(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered=Event();release=Event();second_entered=Event()
    clock=Clock()
    a=GlobalLimiter(tmp_path,clock.now,clock.sleep);b=GlobalLimiter(tmp_path,clock.now,clock.sleep)
    def first():
        entered.set();assert release.wait(2);return response()
    def second():second_entered.set();return response()
    with ThreadPoolExecutor(2) as pool:
        one=pool.submit(a.perform,first,'limit_list_d',{})
        assert entered.wait(2)
        two=pool.submit(b.perform,second,'daily_basic',{})
        assert not second_entered.wait(.05)
        release.set();one.result();two.result()
    assert second_entered.is_set()
