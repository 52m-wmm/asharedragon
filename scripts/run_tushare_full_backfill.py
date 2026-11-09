"""Authorized core-only backfill: two pilot months, checkpoint/audit each month.
No network without --execute. Re-run safely skips completed partitions.
"""
import argparse
import json
import traceback
from collections import Counter,defaultdict
from datetime import date,datetime,timezone
from pathlib import Path
from sqlalchemy import select,func,text
from app.db.session import SessionLocal,engine
from app.models import ProxyBatch,ProxyPartition,Event,Raw,ReconstructionFact,Daily,Stock
from app.providers.tushare_proxy import ProxyClient
from app.collectors.tushare_history import new_batch,run_batch
from app.tushare_cli import month_ranges
from app.services.calendar import completed_date,sessions
from app.models import TradingDay
from audit_tushare_month import audit

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'data/v031-run-state.json'
REPORT=ROOT/'docs/tushare_full_backfill.md'
FIELDS=('first_limit_time','last_limit_time','consecutive_limit_days','open_times','seal_amount','circulating_market_cap')


def checkpoint(state):
    tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2));tmp.replace(STATE)


def report(db,end,source,state):
    months=[];years=defaultdict(lambda:{'sessions':0,'success':0,'up':0,'down':0,'broken':0})
    # Monthly source evidence; missing months are explicitly listed, never zero-filled facts.
    for start,stop in month_ranges(date(2020,1,1),end):
        a=audit(db,start,stop,source);month=str(start)[:7]
        a['month']=month
        parts=list(db.scalars(select(ProxyPartition).where(ProxyPartition.source_id==source,ProxyPartition.endpoint=='limit_list_d',ProxyPartition.trade_date.between(start,stop))))
        a['partition_failures']=[{'date':str(p.trade_date),'status':p.status,'error':p.error_message} for p in parts if p.status!='success' or p.error_message]
        a['anomaly_count']=len(a['invalid_codes'])+a['invalid_dates']+len(a['lifecycle_conflicts'])+a['source_duplicate_rows']
        a['missing_field_cells']=sum(v['denominator']-v['present'] for v in a['field_coverage_up_only'].values())
        a['verification']='independent_overlap_compared_not_certified' if a['comparison']['common_classified_stock_days'] else 'source_only_not_independently_verified'
        months.append(a);y=years[start.year];y['sessions']+=a['calendar_sessions'];y['success']+=len(a['success_dates'])
        for k in ('up','down','broken'):y[k]+=a['counts'].get(k,0)
    # Separate AKShare observed-event comparison for overlapping dates/categories.
    good_parts=list(db.scalars(select(ProxyPartition).where(ProxyPartition.source_id==source,ProxyPartition.endpoint=='limit_list_d',ProxyPartition.status=='success',ProxyPartition.trade_date.between(date(2020,1,1),end))))
    proxy_by_day=defaultdict(list)
    for p in good_parts:
        if set(p.parameters)=={'trade_date'}:
            proxy_by_day[p.trade_date]=list(db.scalars(select(Event).where(Event.raw_id==p.raw_id)))
    from app.services.queries import latest_events
    observed=latest_events(db,'akshare',date(2020,1,1),end)
    ak_by_day=defaultdict(list)
    for e in observed:ak_by_day[e.trade_date].append(e)
    comparisons=[]
    for d in sorted(set(proxy_by_day)&set(ak_by_day)):
        for kind in ('up','down','broken'):
            # Empty/failed AKShare category is unverified, not a zero-event reference.
            from app.models import Coverage
            cov=db.scalar(select(Coverage).where(Coverage.provider=='akshare',Coverage.trade_date==d,Coverage.dataset==kind))
            if not cov or cov.status!='success':continue
            a={e.stock_code:e for e in ak_by_day[d] if e.limit_type==kind}
            b={e.stock_code:e for e in proxy_by_day[d] if e.limit_type==kind}
            comparisons.append({'date':str(d),'type':kind,'ak_count':len(a),'proxy_count':len(b),'common':len(set(a)&set(b)),
                                'proxy_only':sorted(set(b)-set(a)),'ak_only':sorted(set(a)-set(b)),
                                'height_differences':[c for c in set(a)&set(b) if kind=='up' and a[c].consecutive_limit_days!=b[c].consecutive_limit_days],
                                'note':'Different scopes/updates possible; neither source is assumed ground truth.'})
    # Original failed attempts and recoveries remain locally archived.
    retries=[]
    archive=ROOT/'data/tushare_proxy'/source/'limit_list_d'
    for path in sorted(archive.glob('*.json')):
        item=json.loads(path.read_text())
        failed=[a for a in item.get('attempts',[]) if a.get('status')!='success']
        if failed:retries.append({'date':item.get('params',{}).get('trade_date'),'attempts':failed,'archive':str(path.relative_to(ROOT))})
    result={'state':state,'end':str(end),'source_id':source,'years':dict(years),'months':months,'akshare_comparisons':comparisons,'retry_records':retries,
            'existing_daily_counts':dict(db.execute(select(Daily.provider,func.count()).group_by(Daily.provider)).all()),
            'existing_event_counts':dict(db.execute(select(Event.provider,func.count()).group_by(Event.provider)).all()),
            'reconstruction_rows':db.scalar(select(func.count()).select_from(ReconstructionFact))}
    tmp=ROOT/'data/v031-full-audit.tmp';tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str));tmp.replace(ROOT/'data/v031-full-audit.json')
    total=sum(y['success'] for y in years.values());expected=sum(y['sessions'] for y in years.values())
    lines=['# AShareDragon v0.3.1 核心历史回填报告','',
           f"目标：2020-01-01 至 {end}（上海时间已完成交易日）。当前状态：{state['status']}。",'',
           f'交易日成功覆盖：{total}/{expected}。代理观测事件总数：{sum(y[k] for y in years.values() for k in ("up","down","broken"))}。', '',
           '所有“成功”仅表示来源请求、日期、结构与入库检查通过，不等于独立确认准确。没有独立重叠数据的历史月份保持待验证。', '',
           '## 前置检查与安全策略','',
           '现有 2020-12 的 23 天复用；先试采 2022-01、2025-01，通过后才推进其余月份。原 AKShare/BaoStock 记录不修改。',
           '实时 trade_cal 认证已通过；标准 API 不提供 Token 到期时间和代理总配额，不能推断有效期。采用每次至少 3 秒间隔、45 秒读取超时、最多 2 次额外重试；限频/上游池耗尽等待 60 秒。',
           '单次上限 2500；达上限按类型/交易所缩小，无法证明完整则暂停。空响应独立标记，不填零。任一分区经重试后仍失败，或核心涨停字段空值超过 5%，安全暂停。存储错误同样暂停。',
           '按日提交事务，每月立即写审计与进度；恢复不会重新请求成功完整日期。原始响应在 data/tushare_proxy，Token 不写日志、报告或 Git。HTTP 已经用户授权，禁止向官方域名或重定向目标发送凭据。','',
           '## 年度覆盖','', '| 年份 | 交易日 | 成功日 | 覆盖率 | 涨停 | 炸板 | 跌停 |','|---|---:|---:|---:|---:|---:|---:|']
    for y,v in sorted(years.items()):lines.append(f"| {y} | {v['sessions']} | {v['success']} | {v['success']/v['sessions']:.2%} | {v['up']} | {v['broken']} | {v['down']} |")
    lines+=['','## 月度覆盖与字段审计','','核心字段顺序：首次封板、最后封板、连板高度、开板次数、封单金额、流通市值；分母仅为涨停记录。— 表示没有可计算的样本，不是 0%。','',
            '| 月份 | 交易日/成功 | 涨停 | 炸板 | 跌停 | 核心字段非空率 | 异常数/空字段格数 | 状态 |',
            '|---|---|---:|---:|---:|---|---|---|']
    for a in months:
        rates='/'.join(f"{a['field_coverage_up_only'][k]['rate']:.1%}" if a['field_coverage_up_only'][k]['rate'] is not None else '—' for k in FIELDS)
        status='来源完整、独立准确性待验证' if a['passed'] else '尚未采集或存在缺口'
        if a['comparison']['common_classified_stock_days']:status+='；已有重叠样本对照'
        lines.append(f"| {a['month']} | {a['calendar_sessions']}/{len(a['success_dates'])} | {a['counts'].get('up',0)} | {a['counts'].get('broken',0)} | {a['counts'].get('down',0)} | {rates} | {a['anomaly_count']}/{a['missing_field_cells']} | {status} |")
    lines+=['','## 缺失日期与异常','']
    for a in months:
        if a['missing_dates'] or a['partition_failures'] or a['lifecycle_conflicts'] or a['missing_field_cells']:
            lines += [f"- {a['month']}：缺失 {', '.join(a['missing_dates']) or '无'}；错误 {json.dumps(a['partition_failures'],ensure_ascii=False)}；生命周期冲突 {a['lifecycle_conflicts']}；空字段格数 {a['missing_field_cells']}。"]
    lines+=['','## 独立来源交叉核验','', 'BaoStock 对照只在双方有数据、重建可判定的股票交易日内计算；供应商 ST/交易所范围差异尚未归因，不把差异直接视为某一方错误。', '']
    for a in months:
        if a['comparison']['common_classified_stock_days']:
            lines += [f"- {a['month']} BaoStock："+json.dumps(a['comparison'],ensure_ascii=False)]
    lines+=['','AKShare 观测对照（未采集/未确认的类别不按零事件比较）：','```json',json.dumps(comparisons,ensure_ascii=False,indent=2),'```','',
            '## 失败与重试记录','', '以下来自本地不可变归档；同一失败可能同时出现在失败文件和后续成功文件的 attempts 中，不应把归档条数当独立失败次数。','```json',json.dumps(retries,ensure_ascii=False,indent=2),'```','',
            '## 是否可用于后续连板标签','',
            '核心来源日期齐全且字段/范围通过审计的区间，可进入连板计算和独立核验阶段；有缺口的区间不能跨缺口接续。当前状态未完成时，不能宣称全量满足条件。即使来源完整，也须统一 ST、停牌、IPO、北交所与供应商连板定义；本轮不生成正式标签。', '',
            '## 恢复命令','', '```bash',f'.venv/bin/python scripts/run_tushare_full_backfill.py --execute --end {end}', '```','',
            '仅重生成审计报告：去掉 --execute。运行状态：data/v031-run-state.json；逐月机器报告：data/v031-full-audit.json；采集日志：data/v031-full.log。未推送 GitHub。']
    tmp=REPORT.with_suffix('.tmp');tmp.write_text('\n'.join(lines)+'\n');tmp.replace(REPORT)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute',action='store_true');p.add_argument('--end',type=date.fromisoformat)
    p.add_argument('--max-requests',type=int,default=2200)
    a=p.parse_args()
    client=ProxyClient(accept_http=True,max_requests=a.max_requests,overrides={'limit_list_d':{'interval':3.0}})
    with engine.connect() as lock,SessionLocal() as db:
        end=a.end or db.scalar(select(func.max(TradingDay.trade_date)).where(TradingDay.trade_date<=completed_date(),TradingDay.exchange=='SSE'))
        if end>completed_date() or end<date(2020,12,31):p.error('invalid completed-date boundary')
        if not a.execute:
            state=json.loads(STATE.read_text()) if STATE.exists() else {'status':'not_started'}
            report(db,end,client.source_id,state);return
        if not lock.scalar(text('SELECT pg_try_advisory_lock(68142721)')):p.error('another proxy collector is running')
        state={'status':'running','target_end':str(end),'started_at':datetime.now(timezone.utc).isoformat(),'requests_this_run':0}
        checkpoint(state)
        pilots=[(date(2022,1,1),date(2022,1,31)),(date(2025,1,1),date(2025,1,31))]
        try:
            baseline=audit(db,date(2020,12,1),date(2020,12,31),client.source_id)
            if not baseline['passed']:raise RuntimeError('2020-12 baseline no longer complete')
            for start,stop in pilots+list(month_ranges(date(2020,1,1),end)):
                current=audit(db,start,stop,client.source_id)
                if current['passed']:
                    print(json.dumps({'month':str(start)[:7],'status':'already_complete','requests':0}),flush=True);continue
                state.update(month=str(start)[:7],phase='pilot' if (start,stop) in pilots else 'backfill');checkpoint(state)
                batch=new_batch(db,client,start,stop,['limit_list_d'],{'safety_stop':True})
                state['batch_id']=batch.batch_id;checkpoint(state)
                outcome=run_batch(db,client,batch)
                current=audit(db,start,stop,client.source_id)
                state['requests_this_run']=client.used
                if outcome['status']!='success' or not current['passed']:
                    state.update(status='paused',reason=outcome.get('reason') or 'month audit failed',missing_dates=current['missing_dates'],lifecycle_conflicts=current['lifecycle_conflicts'])
                    checkpoint(state);report(db,end,client.source_id,state);print(json.dumps(state),flush=True);return
                state['last_complete_month']=str(start)[:7];checkpoint(state);report(db,end,client.source_id,state)
                print(json.dumps({'month_complete':str(start)[:7],'counts':current['counts'],'requests_this_run':client.used}),flush=True)
            state.update(status='complete',requests_this_run=client.used);checkpoint(state);report(db,end,client.source_id,state)
        except BaseException as exc:
            db.rollback();state.update(status='paused',reason=client.safe(str(exc))[:900],requests_this_run=client.used)
            checkpoint(state)
            try:report(db,end,client.source_id,state)
            except Exception:print('Report refresh failed; previous atomic report and run-state retained.',flush=True)
            print(json.dumps(state),flush=True)
            if isinstance(exc,KeyboardInterrupt):return
            raise
        finally:client.session.close()

if __name__=='__main__':main()
