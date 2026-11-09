"""Targeted, resumable BaoStock state-only collection; no OHLC or database writes."""
from collections import defaultdict
from datetime import date,datetime,timezone,timedelta
from pathlib import Path
import argparse,csv,hashlib,json
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from app.db.session import engine
from app.providers.client import Client
from app.providers.baostock import to_vendor
from app.research.historical_state import normalize_status

BASE=Path.home()/'Datasets/asharedragon/research'
ROOT=BASE/'dragon_v052'


def save(path,obj):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(obj,default=str,ensure_ascii=False,indent=2));tmp.replace(path)


def plan():
    grouped=defaultdict(set)
    for r in csv.DictReader((BASE/'dragon_diagnostics_v051/cycle_exclusions.csv').open()):
        if r['left_reason']=='historical_st_missing' or (r['left_reason']=='established' and r['right_reason']=='historical_st_missing'):
            for key in ('left_boundary_date','first_observed_date','right_boundary_date'):
                if r.get(key):grouped[r['stock_code']].add(date.fromisoformat(r[key]))
    tasks=[dict(stock_code=c,start=str(min(ds)-timedelta(days=7)),end=str(max(ds)+timedelta(days=7)),purpose='693_blocked_cycles') for c,ds in sorted(grouped.items())]
    for code,year in [('002432.SZ',2021),('000957.SZ',2022),('603178.SH',2023),('603960.SH',2024),('603580.SH',2024)]:
        tasks.append(dict(stock_code=code,start=f'{year-1}-12-01',end=f'{year+1}-01-31',purpose='historical_case'))
    for t in tasks:t['key']=hashlib.sha256(json.dumps(t,sort_keys=True).encode()).hexdigest()[:20]
    return tasks


def collect(fetch=True):
    ROOT.mkdir(parents=True,exist_ok=True);rawdir=ROOT/'status_raw';rawdir.mkdir(exist_ok=True)
    tasks=plan();save(ROOT/'status_plan.json',tasks)
    progress=json.loads((ROOT/'status_progress.json').read_text()) if (ROOT/'status_progress.json').exists() else {}
    observations=[];references=[];cross=[]
    with engine.connect() as c,c.begin():
        c.execute(text('SET TRANSACTION READ ONLY'))
        records=[dict(x) for x in c.execute(text("select id,provider,endpoint,fetched_at,sha256,raw_payload from raw_observations where provider='baostock' and ingestion_status='success' and endpoint in ('query_history_k_data_plus','query_daily_history_k_AStock') order by id")).mappings()]
        cross=[dict(x) for x in c.execute(text("select endpoint,ingestion_status,count(*) rows from raw_observations where endpoint in ('stock_st','stk_limit') group by 1,2")).mappings()]
    for r in records:
        for row in r['raw_payload'].get('records',[]):
            if not row.get('code') or not row.get('date') or row.get('adjustflag')!='3':continue
            observations.append(normalize_status(row,source='baostock',observed_at=r['fetched_at'].isoformat(),evidence_id=f"pg_raw:{r['id']}:{r['sha256']}"))
            if all(row.get(k) not in (None,'') for k in ('open','high','low','close','preclose')):
                references.append({**observations[-1],**{k:float(row[k]) for k in ('open','high','low','close')},'pre_close':float(row['preclose'])})
    # A preserved free-provider probe is outside PostgreSQL. Preserve unknown
    # acquisition time as NULL, never substitute filesystem mtime for fetch time.
    probe=Path('data/baostock_probes/fullmarket_20240102.json')
    if probe.exists():
        payload=json.loads(probe.read_text())
        if payload.get('source')!='baostock':raise ValueError('Unexpected cached probe provider')
        digest=hashlib.sha256(probe.read_bytes()).hexdigest()
        for row in payload['records']:
            if row.get('date')!='2024-01-02' or row.get('adjustflag')!='3':raise ValueError('Unexpected cached probe scope')
            item=normalize_status(row,source='baostock',observed_at=payload.get('fetched_at'),evidence_id=f'file:{probe}:sha256={digest}')
            observations.append(item)
            if all(row.get(k) not in (None,'') for k in ('open','high','low','close','preclose')):
                references.append({**item,**{k:float(row[k]) for k in ('open','high','low','close')},'pre_close':float(row['preclose'])})
    # Reuse only individually verified suspension notices from the previous audit;
    # never import its hypothetical non-ST price scenarios as state observations.
    case_path=BASE/'dragon_diagnostics_v051/case_stock_days.parquet'
    if case_path.exists():
        for row in pq.read_table(case_path).to_pylist():
            key=row.get('suspension_notice_key')
            if not key:continue
            pdf=BASE/'dragon_diagnostics_v051/sources'/f'{key}.pdf'
            digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
            if digest!=row['suspension_source_sha256']:raise ValueError('Suspension notice digest changed')
            observations.append(dict(stock_code=row['stock_code'],trade_date=row['trade_date'],is_st=None,trade_status=False,
                source='official_company_suspension_notice',observed_at_utc=row['suspension_source_observed_at_utc'].isoformat(),
                evidence_id=f"{row['suspension_source_url']}#sha256={digest}"))
    failures=0
    for task in tasks:
        path=rawdir/(task['key']+'.json')
        if path.exists():
            previous=progress.get(task['key'],{})
            if previous.get('sha256') and previous['sha256']!=hashlib.sha256(path.read_bytes()).hexdigest():raise ValueError('Collected state digest changed')
            payload=json.loads(path.read_text())
        elif fetch and failures<3:
            params=dict(code=to_vendor(task['stock_code']),fields='date,code,isST,tradestatus',start_date=task['start'],end_date=task['end'],frequency='d',adjustflag='3')
            try:
                result=Client('baostock',retries=0,interval=.5,timeout=45).fetch('query_history_k_data_plus',**params)
                payload=dict(params=params,observed_at_utc=datetime.now(timezone.utc).isoformat(),response=result)
                seen=set()
                for row in result['records']:
                    n=normalize_status(row,source='baostock',observed_at=payload['observed_at_utc'],evidence_id=task['key'])
                    if n['stock_code']!=task['stock_code'] or not task['start']<=str(n['trade_date'])<=task['end'] or n['trade_date'] in seen:raise ValueError('Unexpected code/date/duplicate status')
                    seen.add(n['trade_date'])
                # Empty responses remain documented gaps, not successful coverage.
                save(path,payload);failures=0
            except Exception as exc:
                failures+=1;progress[task['key']]=dict(status='failed',error=str(exc)[:400],at=datetime.now(timezone.utc).isoformat())
                save(ROOT/'status_progress.json',progress);print(task['stock_code'],'failed',flush=True);continue
        else:
            progress.setdefault(task['key'],dict(status='pending',reason='circuit_open' if failures>=3 else 'offline'))
            continue
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        for row in payload['response']['records']:
            observations.append(normalize_status(row,source='baostock',observed_at=payload['observed_at_utc'],evidence_id=f'file:{path.name}:{digest}'))
        progress[task['key']]=dict(status='success' if payload['response']['records'] else 'empty',rows=len(payload['response']['records']),sha256=digest)
        save(ROOT/'status_progress.json',progress)
    save(ROOT/'status_progress.json',progress)
    unique={(r['stock_code'],r['trade_date'],r['evidence_id']):r for r in observations}
    tmp=ROOT/'status_observations.tmp';pq.write_table(pa.Table.from_pylist(list(unique.values())),tmp,compression='zstd');tmp.replace(ROOT/'status_observations.parquet')
    tmp=ROOT/'reference_observations.tmp';pq.write_table(pa.Table.from_pylist(references),tmp,compression='zstd');tmp.replace(ROOT/'reference_observations.parquet')
    summary=dict(tasks=len(tasks),observations=len(unique),local_cross_checks=cross,stock_st_available=any(x['endpoint']=='stock_st' for x in cross),stk_limit_available=any(x['endpoint']=='stk_limit' for x in cross),progress={s:sum(x['status']==s for x in progress.values()) for s in ('success','empty','failed','pending')})
    save(ROOT/'status_summary.json',summary);print(summary,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--offline',action='store_true');args=p.parse_args();collect(not args.offline)
