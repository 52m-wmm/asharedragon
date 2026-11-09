"""Resume free AKShare daily collection after the 10-stock gate; no PostgreSQL writes.

Completed symbols are immutable local checkpoints. Four worker subprocesses maximum,
with globally spaced request starts and a circuit breaker for sustained failures.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import date
import fcntl
import hashlib
import json
from pathlib import Path
import threading
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq
import time

import duckdb
from app.providers.client import Client
from app.providers.normalize import daily_ak, stock_code

START=date(2026,3,16)
END=date(2026,11,6)
SOURCE='akshare_em_unadjusted_20260316_20261106'
LOCAL=Path('data/daily_extension')
ROOT=Path.home()/'Datasets/asharedragon'


def atomic(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,default=str))
    tmp.replace(path)


def normalize(payload,code,calendar):
    rows=[];seen=set()
    for raw in payload['records']:
        if raw.get('股票代码') is not None and stock_code(str(raw['股票代码']).zfill(6))!=code:
            raise ValueError('Returned symbol differs from request')
        row=daily_ak(raw,code)
        d=row['trade_date']
        if not START<=d<=END: continue  # sample overlap remains solely in the purchased source
        if d not in calendar or d in seen:raise ValueError('Non-session or duplicate day')
        seen.add(d)
        for name in ('volume','amount'):
            if row[name] is None or row[name]<0:raise ValueError('Missing/negative volume or amount')
        rows.append({**{k:float(row[k]) for k in ('open','high','low','close','volume','amount')},
            'stock_code':code,'trade_date':str(d),'source':SOURCE,'adjustment_type':'none',
            'pre_close':None,'adj_factor':None,'vwap':None,'trade_status':None,'is_st':None})
    return rows


def universe():
    stocks={r['stock_code']:r for r in json.loads((LOCAL/'stocks.json').read_text()) if r['stock_code'].endswith(('.SH','.SZ'))}
    # Live exchange rosters supplement (never replace) the historical delisted catalogue.
    for path in LOCAL.glob('roster_*.json'):
        if '_error' in path.name:continue
        for r in json.loads(path.read_text())['records']:
            code=r.get('证券代码',r.get('A股代码'))
            if code is None:continue
            try:code=stock_code(str(code).zfill(6))
            except ValueError:continue
            ipo=r.get('上市日期',r.get('A股上市日期'))
            ipo=str(ipo)[:10] if ipo else None
            if code not in stocks:stocks[code]={'stock_code':code,'listing_date':ipo,'delisting_date':None,'provider':'akshare_exchange_roster','status':'current_roster'}
    atomic(LOCAL/'universe.json',list(stocks.values()))
    return sorted(c for c,r in stocks.items() if (not r['listing_date'] or r['listing_date']<=str(END)) and (not r['delisting_date'] or r['delisting_date']>=str(START)))


def collect(workers=4):
    gate=json.loads((LOCAL/'sample_gate.json').read_text())
    if not gate['passed'] or gate['end']!=str(END) or gate['start']!=str(START):raise ValueError('10-stock overlapping-date validation has not passed')
    staging=ROOT/'staging/stocks/daily'/SOURCE
    staging.mkdir(parents=True,exist_ok=True)
    cache=staging/'symbols';cache.mkdir(exist_ok=True)
    with (staging/'.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        codes=universe();calendar={date.fromisoformat(r['trade_date']) for r in json.loads((LOCAL/'calendar.json').read_text())}
        progress={'source':SOURCE,'start':str(START),'end':str(END),'universe_size':len(codes),'symbols':{},'status':'running'}
        path=staging/'progress.json'
        if path.exists():progress=json.loads(path.read_text())
        progress.update(status='running',universe_size=len(codes))
        pending=[]
        for c in codes:
            checkpoint=cache/f'{c}.json'
            if checkpoint.exists():
                saved=json.loads(checkpoint.read_text())
                if saved['source']!=SOURCE or saved['code']!=c:raise ValueError('Mismatched checkpoint')
                progress['symbols'][c]={'status':'success' if saved['rows'] else 'empty_unverified','rows':len(saved['rows'])}
            else:pending.append(c)
        atomic(path,progress)
        limiter=threading.Lock();next_start=[0.]
        def fetch(c):
            sample=LOCAL/f'sample_{c}.json'
            if sample.exists():payload=json.loads(sample.read_text())
            else:
                with limiter:
                    delay=max(0,next_start[0]-time.monotonic())
                    if delay:time.sleep(delay)
                    next_start[0]=time.monotonic()+.5  # at most two new requests/second
                payload=Client('akshare',retries=0,interval=0,timeout=30).fetch('stock_zh_a_hist',symbol=c[:6],period='daily',start_date='20260316',end_date='20261106',adjust='')
            rows=normalize(payload,c,calendar)
            atomic(cache/f'{c}.json',{'code':c,'source':SOURCE,'rows':rows,'raw':payload})
            return {'status':'success' if rows else 'empty_unverified','rows':len(rows)}
        consecutive_failures=0;stopped=False;iterator=iter(pending)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures={}
            for _ in range(workers):
                c=next(iterator,None)
                if c:futures[pool.submit(fetch,c)]=c
            while futures:
                done,_=wait(futures,return_when=FIRST_COMPLETED)
                for future in done:
                    c=futures.pop(future)
                    try:
                        progress['symbols'][c]=future.result();consecutive_failures=0
                    except Exception as exc:
                        consecutive_failures+=1
                        progress['symbols'][c]={'status':'failed','error':str(exc)[:500]}
                    if consecutive_failures>=8:stopped=True
                    if not stopped:
                        nxt=next(iterator,None)
                        if nxt:futures[pool.submit(fetch,nxt)]=nxt
                atomic(path,progress)
                n=len(progress['symbols'])
                if n%25<workers or stopped:print(n,'/',len(codes),'rows',sum(r.get('rows',0) for r in progress['symbols'].values()),'stopped',stopped,flush=True)
        progress['status']='paused_source_failures' if stopped else 'collected_with_gaps'
        progress['not_attempted']=[c for c in codes if c not in progress['symbols']]
        atomic(path,progress)
        publish(staging)


def publish(staging):
    progress=json.loads((staging/'progress.json').read_text())
    destination=ROOT/'processed/stocks/daily'/SOURCE
    destination.mkdir(parents=True,exist_ok=True)
    db=duckdb.connect(config={'memory_limit':'512MB','threads':'2'})
    files=[str(p) for p in (staging/'symbols').glob('*.json')]
    if not files:return
    # Stream only normalized rows: inferring thousands of nested raw JSON schemas
    # exceeds the memory budget even when the final normalized table is small.
    schema=pa.schema([(c,pa.float64()) for c in ('open','high','low','close','volume','amount')]+[
        ('stock_code',pa.string()),('trade_date',pa.date32()),('source',pa.string()),('adjustment_type',pa.string()),
        ('pre_close',pa.float64()),('adj_factor',pa.float64()),('vwap',pa.float64()),('trade_status',pa.bool_()),('is_st',pa.bool_())])
    with tempfile.TemporaryDirectory(prefix='.publish-',dir=staging) as temporary_directory:
        normalized_path=Path(temporary_directory)/'normalized.parquet'
        buffer=[]
        with pq.ParquetWriter(normalized_path,schema,compression='zstd') as writer:
            for file in files:
                checkpoint=json.loads(Path(file).read_text())
                if checkpoint['source']!=SOURCE:raise ValueError('Unexpected checkpoint source')
                for row in checkpoint['rows']:
                    buffer.append({**row,'trade_date':date.fromisoformat(row['trade_date'])})
                if len(buffer)>=20000:
                    writer.write_table(pa.Table.from_pylist(buffer,schema=schema));buffer.clear()
            if buffer:writer.write_table(pa.Table.from_pylist(buffer,schema=schema))
        db.read_parquet(str(normalized_path)).create_view('bars')
        duplicates=db.sql('SELECT count(*) FROM (SELECT stock_code,trade_date FROM bars GROUP BY ALL HAVING count(*)>1)').fetchall()[0][0]
        if duplicates:raise ValueError('Duplicate keys in checkpoints')
        result={'status':'validated_partial','source':SOURCE,'adjustment_type':'none','volume_unit':'shares','amount_unit':'CNY',
            'coverage':'provider observations; absent rows are unresolved, not fabricated suspensions','months':{},'sample_gate':json.loads((LOCAL/'sample_gate.json').read_text())}
        for (month,) in db.sql("SELECT DISTINCT strftime(trade_date,'%Y-%m') AS month FROM bars ORDER BY 1").fetchall():
            relative=f'year={month[:4]}/month={month[5:]}/daily.parquet';output=destination/relative
            output.parent.mkdir(parents=True,exist_ok=True);temporary=output.with_suffix('.partial');temporary.unlink(missing_ok=True)
            try:
                db.sql("SELECT * FROM bars WHERE strftime(trade_date,'%Y-%m')=? ORDER BY stock_code,trade_date",params=[month]).write_parquet(str(temporary),compression='zstd')
                n=db.read_parquet(str(temporary)).count('*').fetchall()[0][0]
                temporary.replace(output)
                result['months'][month]={'file':relative,'rows':n,'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}
            finally:temporary.unlink(missing_ok=True)
        atomic(destination/'manifest.json',result)
        print('published',destination,sum(r['rows'] for r in result['months'].values()),flush=True)
    db.close()



if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workers',type=int,choices=range(1,5),default=4)
    a=p.parse_args();collect(a.workers)
