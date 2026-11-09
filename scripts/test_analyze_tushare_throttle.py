import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('throttle',Path(__file__).with_name('analyze_tushare_throttle.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_operator_events_and_unfinished_requests_are_not_successes():
    rows=[{'event':'operator_review_one_probe'},
          {'event':'request_start','sequence':1,'started_at':'2026-11-06T01:00:00+00:00','params':{}},
          {'event':'request_end','sequence':1,'finished_at':'2026-11-06T01:00:01+00:00','status':'ip_rate_limited'},
          {'event':'request_start','sequence':2,'started_at':'2026-11-06T01:01:31+00:00','params':{}}]
    result=m.analyze(rows)
    assert result['requests']==2 and result['ip_limits']==1
    assert result['cooldown_outcomes']=={'90_seconds':{'pending_or_not_attempted':1}}
    assert result['window_buckets']['1']=={'requests':2,'ip_limited':1}
