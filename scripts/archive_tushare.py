"""Day-pass archive runner. Source responses first; additive existing DB partitions.
Run with --execute. Stops on persistent shared quota; never bypasses provider limits.
"""
import argparse
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from sqlalchemy import select, func, text, cast
from sqlalchemy.dialects.postgresql import JSONB
from app.db.session import SessionLocal, engine
from app.models import ProxyPartition, Raw, TradingDay
from app.providers.tushare_proxy import ProxyClient, ProxyError, digest
from app.collectors.tushare_history import collect_partition, new_batch
from app.services.calendar import sessions, completed_date

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/raw/tushare'
STATE=ROOT/'data/tushare-archive-state.json'

def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,default=str);f.flush();os.fsync(f.fileno())
    tmp.replace(path)

def now():return datetime.now(timezone.utc).isoformat()

def verified(path):
    if not path.exists():raise FileNotFoundError(path)
    value=json.loads(path.read_text())
    if digest(value['payload'])!=value['response_hash']:raise RuntimeError('archive hash mismatch: '+str(path))
    return value

def archive(endpoint,d,category,payload,status,source):
    path=BASE/endpoint/str(d.year)/f'{d.month:02d}'/(f'{d:%Y%m%d}_{category}.json')
    old=verified(path) if path.exists() else None
    value={'dataset':endpoint,'trade_date':str(d),'category':category,'source_id':source,
           'request_status':status,'response_hash':digest(payload),'archived_at':now(),'payload':payload}
    if old and old['response_hash']!=value['response_hash']:
        atomic(path.with_name(path.stem+'_'+old['response_hash'][:16]+'.json'),old)
    atomic(path,value)
    return str(path.relative_to(ROOT)),value['response_hash']

def persist(state):
    state['updated_at']=now();atomic(STATE,state)
    # Whole state is the durable task index; monthly snapshots support independent recovery.
    if state.get('month'):
        atomic(BASE/'checkpoints'/state['dataset']/(state['month']+'.json'),state)

def sync_existing(db,client,state):
    """No requests: materialize existing durable source evidence and error attempts."""
    parts=list(db.scalars(select(ProxyPartition).where(ProxyPartition.source_id==client.source_id,ProxyPartition.endpoint=='limit_list_d').order_by(ProxyPartition.id)))
    for part in parts:
        if set(part.parameters)-{'trade_date','limit_type'}:continue
        raw=db.get(Raw,part.raw_id) if part.raw_id else None
        categories=[part.parameters['limit_type']] if 'limit_type' in part.parameters else ['U','D','Z']
        if not raw or part.status!='success':
            if 'limit_type' not in part.parameters:continue
            evidence=[]
            for path in (client.archive/'limit_list_d').glob('*.json'):
                v=json.loads(path.read_text())
                if v.get('params')==part.parameters:evidence.append(v)
            payload={'failed_response_evidence':evidence,'records':[]}
        else:payload=raw.raw_payload
        key='limit_list_d:'+str(part.trade_date)
        task=state['tasks'].setdefault(key,{'dataset':'limit_list_d','trade_date':str(part.trade_date),'expected_categories':['U','D','Z'],'categories':{}})
        for category in categories:
            subset={'original_source_payload':payload,'selected_status':category,
                    'records':[r for r in payload.get('records',[]) if r.get('limit')==category]}
            status='success' if part.status=='success' and not part.error_message else 'failed'
            path,h=archive('limit_list_d',part.trade_date,category,subset,status,client.source_id)
            attempts=[a for e in payload.get('failed_response_evidence',[]) for a in e.get('attempts',[])]
            task['categories'][category]={'request_status':status,'row_count':len(subset['records']),'raw_file_path':path,
                 'response_hash':h,'retry_count':max(0,max((len(e.get('attempts',[])) for e in payload.get('failed_response_evidence',[])),default=1)-1),
                 'last_error':part.error_message,'completed_at':str(part.updated_at) if status=='success' else None,'db_raw_id':raw.id if raw else None}
        task['completed_categories']=[k for k,v in task['categories'].items() if v['request_status']=='success']
        task['request_status']='complete' if len(task['completed_categories'])==3 else 'pending_verification'
    atomic(STATE,state)
    for month in sorted({t['trade_date'][:7] for t in state['tasks'].values()}):
        atomic(BASE/'checkpoints'/'limit_list_d'/(month+'.json'),{k:v for k,v in state['tasks'].items() if v['trade_date'].startswith(month)})


def write_report(state):
    from collections import defaultdict
    months=defaultdict(lambda:{'complete_days':0,'partial_days':0,'U':0,'D':0,'Z':0})
    checked=0
    for task in state['tasks'].values():
        if task['dataset']!='limit_list_d':continue
        month=months[task['trade_date'][:7]]
        month['complete_days' if task['request_status']=='complete' else 'partial_days']+=1
        for category,item in task['categories'].items():
            verified(ROOT/item['raw_file_path']);checked+=1
            if item['request_status']=='success':month[category]+=item['row_count']
    lines=['# Tushare 一日权限紧急归档进度','',f"状态：{state['status']}；目标截至 {state.get('end','尚未确定')}。",'',
           '阶段进度以 data/tushare-archive-state.json 为准；只有 U/D/Z 均成功的日期计入完整日。',
           f"当前阻碍：{state.get('last_error','无记录')}",
           '原有 2020-12 完整数据已复用，无重复网络请求。字段未独立核验不会阻止归档；空数据保持待核实。','',
           '| 月份 | 完整日 | 不完整日 | U | D | Z |','|---|---:|---:|---:|---:|---:|']
    for month,counts in sorted(months.items()):lines.append('| '+month+' | '+' | '.join(str(counts[k]) for k in ('complete_days','partial_days','U','D','Z'))+' |')
    lines+=['',f'已验证 {checked} 个状态归档的内容哈希。原始响应保留全部字段、来源和时间。',
            '失败响应与尝试记录保存在 data/tushare_proxy，归档索引在 data/tushare-archive-state.json；源记录不等于独立确认准确。',
            '', '恢复（先确认服务商解除来源 IP 限制）：','```bash','.venv/bin/python scripts/archive_tushare.py --execute >> data/tushare-emergency-archive.log 2>&1','```',
            '', '只整理已有数据并校验归档（不发请求）：','```bash','.venv/bin/python scripts/archive_tushare.py --audit-only','```',
            '', 'P1：kpl_list、limit_list_ths、limit_cpt_list、stock_st、stk_limit；P2：daily_basic。范围以真实响应为准。',
            'daily_basic/stk_limit 达上限后尝试分页，重复页或重叠股票键即标记截断；代理是否实际支持分页仍待真实验证。',
            '未进行 GitHub 推送，AKShare/BaoStock 原始记录未覆盖。']
    (ROOT/'docs/tushare_full_backfill.md').write_text('\n'.join(lines)+'\n')

def write_coverage(db,state,end):
    years={}
    for d in sessions(db,date(2020,1,1),end):
        y=years.setdefault(str(d.year),{'expected_days':0,'complete_days':0,'U':0,'D':0,'Z':0})
        y['expected_days']+=1
        task=state['tasks'].get('limit_list_d:'+str(d),{})
        y['complete_days']+=task.get('request_status')=='complete'
        for k,v in task.get('categories',{}).items():
            if v['request_status']=='success':y[k]+=v['row_count']
    atomic(ROOT/'data/tushare-yearly-coverage.json',{'updated_at':now(),'years':years,'status':state['status']})


class ArchiveClient(ProxyClient):
    def fetch(self,endpoint,refresh=False,**params):
        # A missing normalization partition may already have a complete source response.
        # Empty responses are retained as unverified rather than repeatedly downloaded.
        payload=super().fetch(endpoint,refresh=False,**params)
        d=datetime.strptime(params['trade_date'],'%Y%m%d').date()
        category=str(params.get('limit_type',params.get('tag','all')))
        # Full response is durable before normalization or database work begins.
        archive(endpoint,d,'response_'+category+'_'+digest(params)[:12],payload,
                'received' if payload['records'] else 'empty_unverified',self.source_id)
        return payload


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--audit-only',action='store_true')
    parser.add_argument('--pilot-days',type=int,choices=(2,3,4,5),default=3)
    parser.add_argument('--end',type=date.fromisoformat)
    args=parser.parse_args()
    if not args.execute and not args.audit_only:
        print('Use --execute to archive P0 then P1 then P2; no request made.');return
    client=ArchiveClient(accept_http=True,max_requests=30000,
                         overrides={k:{'interval':3.0} for k in ('limit_list_d','kpl_list','limit_list_ths','limit_cpt_list','stock_st','stk_limit','daily_basic')})
    state=json.loads(STATE.read_text()) if STATE.exists() else {'tasks':{},'runs':[]}
    if not args.audit_only:
        state['runs'].append({'started_at':now()});state['status']='running';state['last_error']=None;state['pilot_days']=[]
    with engine.connect() as lock,SessionLocal() as db:
        if not lock.scalar(text('SELECT pg_try_advisory_lock(68142721)')):raise RuntimeError('another collector is running')
        end=args.end or db.scalar(select(func.max(TradingDay.trade_date)).where(TradingDay.exchange=='SSE',TradingDay.trade_date<=completed_date()))
        if end>completed_date():raise ValueError('end is not a completed session')
        state['end']=str(end)
        sync_existing(db,client,state)
        if args.audit_only:
            write_report(state);client.session.close();return
        scheduler=ROOT/'data/tushare_scheduler/scheduler.json'
        initial_limits=json.loads(scheduler.read_text()).get('ip_limit_events',0) if scheduler.exists() else 0
        plans=[('limit_list_d',date(2020,1,1),[('U',{'limit_type':'U'}),('D',{'limit_type':'D'}),('Z',{'limit_type':'Z'})]),
               ('kpl_list',date(2020,1,1),[('up',{'tag':'涨停'}),('broken',{'tag':'炸板'})]),
               ('limit_list_ths',date(2023,11,1),[('up',{'limit_type':'涨停池'}),('broken',{'limit_type':'炸板池'})]),
               ('limit_cpt_list',date(2020,1,1),[('all',{})]),('daily_basic',date(2020,1,1),[('all',{})])]
        try:
            for endpoint,start,variants in plans:
                state['dataset']=endpoint;batch=None;month=None;failures=0
                for d in sessions(db,start,end):
                    was_complete=state['tasks'].get(endpoint+':'+str(d),{}).get('request_status')=='complete'
                    if month!=str(d)[:7]:
                        if batch is not None:
                            batch.status='archived';batch.finished_at=datetime.now(timezone.utc);db.commit()
                        month=str(d)[:7];state['month']=month
                        batch=new_batch(db,client,d,min(end,date(d.year+(d.month==12),d.month%12+1,1)),[endpoint],{'archive_only_quality_policy':True})
                        batch.status='running';state['batch_id']=batch.batch_id;db.commit()
                    key=endpoint+':'+str(d)
                    task=state['tasks'].setdefault(key,{'dataset':endpoint,'trade_date':str(d),'expected_categories':[v[0] for v in variants],'categories':{}})
                    # Reuse complete prior all-status partitions, including December 2020.
                    old=db.scalar(select(ProxyPartition).where(ProxyPartition.source_id==client.source_id,ProxyPartition.endpoint==endpoint,
                         ProxyPartition.trade_date==d,ProxyPartition.status=='success',ProxyPartition.error_message.is_(None))) if endpoint=='limit_list_d' else None
                    if old and old.parameters=={'trade_date':d.strftime('%Y%m%d')}:
                        raw=db.get(Raw,old.raw_id)
                        for category,_ in variants:
                            payload={'original_combined_response':raw.raw_payload,'selected_status':category,
                                     'records':[r for r in raw.raw_payload['records'] if r['limit']==category],
                                     'reuse_note':'Complete original U/D/Z response retained; no network request.'}
                            path,h=archive(endpoint,d,category,payload,'success',client.source_id)
                            task['categories'][category]={'request_status':'success','row_count':len(payload['records']),'raw_file_path':path,
                                  'response_hash':h,'retry_count':0,'last_error':None,'completed_at':now(),'db_raw_id':raw.id}
                    else:
                        for category,variant in variants:
                            previous=task['categories'].get(category)
                            if previous and previous['request_status'] in ('success','empty_unverified','normalization_pending'):
                                verified(ROOT/previous['raw_file_path']);continue
                            params={'trade_date':d.strftime('%Y%m%d'),**variant}
                            part,n=collect_partition(db,client,batch,endpoint,d,params)
                            raw=db.get(Raw,part.raw_id) if part.raw_id else None
                            # Failed normalization also has archived full payload in Raw.
                            if not raw or part.status!='success':
                                raw=db.scalar(select(Raw).where(Raw.provider=='tushare_proxy',Raw.endpoint==endpoint,Raw.trade_date==d,
                                      cast(Raw.parameters,JSONB)=={'source_id':client.source_id,**params}).order_by(Raw.id.desc()))
                            payload=raw.raw_payload if raw else {}
                            if not payload:
                                failures_evidence=[]
                                for archived in (client.archive/endpoint).glob('*.json'):
                                    item=json.loads(archived.read_text())
                                    if item.get('params')==params:failures_evidence.append(item)
                                payload={'failed_response_evidence':failures_evidence,'records':[]}

                            error=part.error_message or ''
                            status='success' if part.status=='success' and not error else 'empty_unverified' if 'empty_unverified' in error else 'normalization_pending' if payload.get('parts') else 'failed'
                            path,h=archive(endpoint,d,category,payload,status,client.source_id)
                            attempts=[a for p in payload.get('parts',[]) for a in p.get('attempts',[])]
                            task['categories'][category]={'request_status':status,'row_count':len(payload.get('records',[])),
                                 'raw_file_path':path,'response_hash':h,'retry_count':max(0,len(attempts)-1) if attempts else max(0,max((len(e.get('attempts',[])) for e in payload.get('failed_response_evidence',[])),default=1)-1),
                                 'last_error':error or None,'completed_at':now() if status=='success' else None,'db_raw_id':raw.id if raw else None}
                            task['completed_categories']=[k for k,v in task['categories'].items() if v['request_status']=='success']
                            task['request_status']='complete' if len(task['completed_categories'])==len(variants) else 'pending_verification'
                            state['requests_this_run']=client.used;persist(state)
                            print(json.dumps({'dataset':endpoint,'date':str(d),'category':category,'status':status,'rows':len(payload.get('records',[]))}),flush=True)
                            if any(k in error for k in ('rate_limit','upstream_unavailable','budget:')):
                                raise RuntimeError('Provider limit persists after bounded retries: '+error)
                            if 'permission:' in error:
                                failures=3;break
                            failures=failures+1 if status=='failed' else 0
                        if failures>=3:
                            state.setdefault('endpoint_failures',{})[endpoint]=error
                            if endpoint=='limit_list_d':raise RuntimeError('P0 unavailable: '+error)
                            break
                    task['completed_categories']=[k for k,v in task['categories'].items() if v['request_status']=='success']
                    task['request_status']='complete' if len(task['completed_categories'])==len(variants) else 'pending_verification'
                    if endpoint=='limit_list_d' and not was_complete and len(state['pilot_days'])<args.pilot_days:
                        if task['request_status']!='complete':raise RuntimeError('Pilot day incomplete; inspect missing categories before full run')
                        state['pilot_days'].append(str(d))
                        print(json.dumps({'event':'pilot_day_complete','date':str(d),'pilot_days':state['pilot_days']}),flush=True)
                        if len(state['pilot_days'])==args.pilot_days:
                            if scheduler.exists() and json.loads(scheduler.read_text()).get('ip_limit_events',0)>initial_limits:
                                raise RuntimeError('Pilot encountered IP rate limits; inspect request timestamps before full run')
                            print(json.dumps({'event':'pilot_passed_continue_p0'}),flush=True)
                    persist(state)
                    write_coverage(db,state,end)
                if endpoint=='limit_list_d':
                    missing=[str(d) for d in sessions(db,start,end) if state['tasks'].get(endpoint+':'+str(d),{}).get('request_status')!='complete']
                    if missing:raise RuntimeError('P0 gaps remain; P1/P2 deferred: '+str(len(missing)))
                batch.status='partial' if failures else 'archived';db.commit()
            state['status']='finished_with_audit_required'
        except BaseException as exc:
            db.rollback();state['status']='paused';state['last_error']=client.safe(str(exc))[:900]
            if batch is not None:
                batch.status='paused';batch.finished_at=datetime.now(timezone.utc);db.commit()
            print(json.dumps({'status':'paused','reason':state['last_error']}),flush=True)
        finally:
            state['requests_this_run']=client.used;state['runs'][-1].update(finished_at=now(),status=state['status'],requests=client.used)
            persist(state);write_coverage(db,state,end);write_report(state);client.session.close()

if __name__=='__main__':main()
