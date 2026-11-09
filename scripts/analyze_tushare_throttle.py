"""Read-only throttle analysis. Never makes API requests or changes quota state."""
import argparse
from collections import Counter,defaultdict
from datetime import datetime,timezone
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]

def analyze(rows):
    starts=[r for r in rows if r.get('event')=='request_start']
    ends=[r for r in rows if r.get('event')=='request_end']
    end_by_id={r['sequence']:r for r in ends}
    stamp=lambda value:datetime.fromisoformat(value).timestamp()
    buckets=defaultdict(lambda:{'requests':0,'ip_limited':0})
    limits=[];previous=None
    for i,r in enumerate(starts):
        t=stamp(r['started_at']);end=end_by_id.get(r['sequence'])
        window=sum(0<=t-stamp(x['started_at'])<60 for x in starts[:i+1])
        bucket=buckets[str(window)];bucket['requests']+=1
        if end and end['status']=='ip_rate_limited':
            bucket['ip_limited']+=1
            following=starts[i+1] if i+1<len(starts) else None
            nxt=end_by_id.get(following['sequence']) if following else None
            limits.append({'sequence':r['sequence'],'started_at':r['started_at'],'limited_at':end['finished_at'],
               'requests_in_60s':window,'params':r.get('params',{}),
               'previous_start_gap_seconds':round(t-stamp(previous['started_at']),3) if previous else None,
               'next_request_wait_seconds':round(stamp(following['started_at'])-stamp(end['finished_at']),3) if following else None,
               'next_status':nxt['status'] if nxt else 'pending_or_not_attempted',
               'same_query_next':following.get('params')==r.get('params') if following else None})
        previous=r
    cooldowns=defaultdict(Counter)
    for row in limits:
        wait=row['next_request_wait_seconds']
        group='not_retried' if wait is None else '90_seconds' if 89<=wait<=95 else 'other_wait'
        cooldowns[group][row['next_status']]+=1
    return {'requests':len(starts),'completed_requests':len(ends),'ip_limits':len(limits),
            'window_buckets':dict(buckets),'cooldown_outcomes':{k:dict(v) for k,v in cooldowns.items()},'limit_events':limits}

def atomic(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(value);tmp.replace(path)

def refresh():
    source=ROOT/'data/tushare_scheduler/requests.jsonl'
    lines=source.read_text().splitlines();rows=[]
    for i,line in enumerate(lines):
        try:rows.append(json.loads(line))
        except json.JSONDecodeError:
            if i!=len(lines)-1:raise
    result=analyze(rows)
    state=json.loads((ROOT/'data/tushare-archive-state.json').read_text())
    coverage=json.loads((ROOT/'data/tushare-yearly-coverage.json').read_text())
    result.update(updated_at=datetime.now(timezone.utc).isoformat(),collector_status=state['status'],
                  active_range=state.get('active_range'),last_error=state.get('last_error'),
                  completed_days=sum(y['complete_days'] for y in coverage['years'].values()))
    atomic(ROOT/'data/tushare-throttle-analysis.json',json.dumps(result,ensure_ascii=False,indent=2))
    text=['# Tushare 限流观察（自动更新）','',f"更新时间（洛杉矶）：{datetime.now(ZoneInfo('America/Los_Angeles')).isoformat(timespec='seconds')}",
          f"采集状态：{result['collector_status']}；完整交易日：{result['completed_days']}；范围：{result['active_range']}。",
          f"本机真实请求 {result['requests']} 次，限流 {result['ip_limits']} 次。",'',
          '仅观察既有生产请求，不发送额外探测，不调整限速。下表的 response 仅表示收到非 IP 限流响应，不等于业务成功或入库完成。',
          '90 秒恢复是经验观察，不是最短等待时间证明；未进行 30/60 秒对照。单机日志不能识别其他用户共享 IP 的流量，也不能证明服务商实际配额机制。','',
          '| 重试等待区间 | 后续结果 | 次数 |','|---|---|---:|']
    for wait,counts in result['cooldown_outcomes'].items():
        for status,count in counts.items():text.append(f'| {wait} | {status} | {count} |')
    text+=['','| 本机滚动60秒请求数（含当前请求） | 请求数 | 限流数 |','|---|---:|---:|']
    for bucket,counts in sorted(result['window_buckets'].items(),key=lambda x:int(x[0])):
        text.append(f"| {bucket} | {counts['requests']} | {counts['ip_limited']} |")
    text+=['','低频也出现限流只说明本机频率不足以解释全部现象，不证明共享 IP；样本非随机，分组比例不能当作因果关系。',
           '每次限流的请求时间、返回时间、请求间隔、查询范围与下一次结果见 data/tushare-throttle-analysis.json。','']
    atomic(ROOT/'docs/tushare_throttle_observations.md','\n'.join(text))
    print(json.dumps({k:result[k] for k in ('updated_at','collector_status','requests','ip_limits','completed_days','active_range')}),flush=True)
    return state['status']

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--watch',action='store_true');args=parser.parse_args()
    while True:
        status=refresh()
        if not args.watch or status!='running':break
        time.sleep(60)

if __name__=='__main__':main()
