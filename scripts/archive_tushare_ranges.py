"""P0 only: serial weekly ranges, bounded splitting, original checkpoint compatibility."""
import json
from datetime import date,datetime,timezone
from sqlalchemy import text,select
from app.db.session import SessionLocal,engine
from app.models import ProxyPartition,Raw
from app.providers.tushare_proxy import ProxyClient,digest
from app.collectors.tushare_history import collect_partition,new_batch
from app.collectors.tushare_range import fetch_ranges,RangeView
from app.services.calendar import sessions,completed_date
from archive_tushare import ROOT,STATE,atomic,archive,verified,persist,sync_existing,write_report,write_coverage,now

def groups(days,complete,recovery_days=()):
    group=[]
    for d in days:
        if str(d) in complete:
            if group:yield group;group=[]
            continue
        if group and (d.isocalendar()[:2]!=group[0].isocalendar()[:2] or d.month!=group[0].month or ((d.strftime("%Y%m%d") in recovery_days)!=(group[0].strftime("%Y%m%d") in recovery_days))):yield group;group=[]
        group.append(d)
    if group:yield group

def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    if not args.execute:print('Use --execute after the December range probe passes.');return
    proof=json.loads((ROOT/'data/tushare-range-probe.json').read_text())
    if proof['status']!='verified':raise RuntimeError('range baseline has not passed')
    original=json.loads((ROOT/proof['archive']).read_text())
    if digest(original)!= (ROOT/proof['archive']).stem:raise RuntimeError('probe archive hash mismatch')
    c=ProxyClient(accept_http=True,max_requests=10000,overrides={'limit_list_d':{'interval':3}})
    if c.source_id!=original['source_id']:raise RuntimeError('proof belongs to a different source')
    with engine.connect() as lock,SessionLocal() as db:
        if not lock.scalar(text('SELECT pg_try_advisory_lock(68142721)')):raise RuntimeError('another collector is running')
        state=json.loads(STATE.read_text());sync_existing(db,c,state)
        end=date.fromisoformat(state['end'])
        if end>completed_date():raise ValueError('unfinished session boundary')
        days=sessions(db,date(2020,1,1),end)
        complete={t['trade_date'] for t in state['tasks'].values() if t['dataset']=='limit_list_d' and t['request_status']=='complete'}
        for t in state['tasks'].values():
            if t['dataset']=='limit_list_d' and t['trade_date'] in complete:
                for item in t['categories'].values():verified(ROOT/item['raw_file_path'])
        state.update(status='running',dataset='limit_list_d',mode='weekly_adaptive_range',last_error=None)
        state['runs'].append({'started_at':now(),'mode':'range'})
        batch=None;recovery_days=set()
        if state.get('active_payload'):
            c.recovery_payload=verified(ROOT/state['active_payload'])['payload']
            recovery_days={r['trade_date'] for r in c.recovery_payload['records']}
        try:
            for group in groups(days,complete,recovery_days):
                state['month']=str(group[0])[:7]
                state['active_range']=[str(group[0]),str(group[-1])];persist(state)
                for dates,payload in fetch_ranges(c,group):
                    # Full HTTP response saved and hashed before any normalization.
                    path,h=archive('limit_list_d',dates[0],'range_'+str(dates[-1]),payload,'validated_range',c.source_id)
                    state['active_payload']=path;persist(state)
                    batch=new_batch(db,c,dates[0],dates[-1],['limit_list_d'],{'validated_range':True,'source_range_archive':path})
                    batch.status='running';db.commit();state['batch_id']=batch.batch_id
                    view=RangeView(c,payload)
                    for d in dates:
                        task=state['tasks'].setdefault('limit_list_d:'+str(d),{'dataset':'limit_list_d','trade_date':str(d),'expected_categories':['U','D','Z'],'categories':{}})
                        for category in ('U','D','Z'):
                            old=task['categories'].get(category)
                            if old and old['request_status']=='success':verified(ROOT/old['raw_file_path'])
                            params={'trade_date':d.strftime('%Y%m%d'),'limit_type':category}
                            part,n=collect_partition(db,view,batch,'limit_list_d',d,params)
                            if part.status!='success' or part.error_message:raise RuntimeError(part.error_message or 'partition incomplete')
                            raw=db.get(Raw,part.raw_id)
                            expected=view.fetch('limit_list_d',**params)['records']
                            order=lambda r:(r['trade_date'],r['ts_code'],r['limit'])
                            if digest(sorted(raw.raw_payload['records'],key=order))!=digest(sorted(expected,key=order)):
                                raise RuntimeError('existing partition differs from validated range; both retained')
                            raw_path,raw_hash=archive('limit_list_d',d,category,raw.raw_payload,'success',c.source_id)
                            task['categories'][category]={'request_status':'success','row_count':part.row_count,'raw_file_path':raw_path,'response_hash':raw_hash,
                                'field_anomaly_count':part.audit.get('field_anomaly_count',0),'quality':'field_anomalies' if part.audit.get('field_anomaly_count') else 'source_validated','retry_count':max(0,len(payload['attempts'])-1),'last_error':None,'completed_at':now(),'db_raw_id':raw.id,
                                'source_range_archive':path,'completeness':'validated_unfiltered_range_not_independent_accuracy'}
                            task['completed_categories']=[k for k,v in task['categories'].items() if v['request_status']=='success']
                            task['request_status']='complete' if len(task['completed_categories'])==3 else 'partial'
                            state['requests_this_run']=c.used;persist(state)
                        write_coverage(db,state,end)
                    batch.status='success';batch.finished_at=datetime.now(timezone.utc);db.commit()
                    state.setdefault('ranges',{})[str(dates[0])+':'+str(dates[-1])]={'status':'complete','archive':path,'hash':h,'rows':len(payload['records'])}
                    state['active_payload']=None;persist(state)
                    print(json.dumps({'event':'range_complete','start':str(dates[0]),'end':str(dates[-1]),'rows':len(payload['records']),'requests_this_run':c.used,'archive':path}),flush=True)
            state['status']='p0_complete_source_verified'
        except BaseException as exc:
            db.rollback();state.update(status='paused',last_error=c.safe(str(exc))[:900])
            if batch is not None:batch.status='paused';batch.finished_at=datetime.now(timezone.utc);db.commit()
            print(json.dumps({'event':'paused','reason':state['last_error'],'requests':c.used}),flush=True)
        finally:
            state['requests_this_run']=c.used;state['runs'][-1].update(finished_at=now(),status=state['status'],requests=c.used)
            persist(state);write_coverage(db,state,end);write_report(state);c.session.close()

if __name__=='__main__':main()
