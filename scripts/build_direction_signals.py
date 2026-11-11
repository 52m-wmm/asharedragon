"""E3 only: build deterministic directions beside immutable E1/E2 releases."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import tempfile

import duckdb
import pyarrow as pa

from app.analytics.direction_signals import RULE_VERSION, POLICY, breadth_comparison, daily_signals
from app.research.queries import DEFAULT_ROOT
from scripts.build_market_breadth import manifest_path, REPO, calculate, rows, save, sha, snapshot

# No final heights, future delisting dates, cycle outcomes or later labels enter SQL.
PRICE_FIELDS = ('stock_code','trade_date','open','high','low','close','pre_close','price_source',
                'pg_daily_id','price_quality','price_conflict','is_st','st_quality','status_conflict','trade_status')


def checked_release(base):
    path, manifest = snapshot(base)
    if sha(path/'summary.json') != manifest['summary_sha256']:
        raise ValueError('Release summary hash mismatch')
    summary = json.loads((path/'summary.json').read_text())
    for name, expected in summary['files'].items():
        if sha(path/name) != expected:
            raise ValueError(f'Release file hash mismatch: {name}')
    return path, summary


def open_profiles(source, recovery, lifecycle):
    db=duckdb.connect(config={'threads':2,'memory_limit':'1GB'})
    db.execute('ATTACH ? AS frozen (READ_ONLY)',[str(source/'research.duckdb')])
    db.execute('CREATE SCHEMA src')
    db.execute('CREATE VIEW src.stock_daily AS SELECT '+','.join(PRICE_FIELDS)+' FROM frozen.stock_daily')
    db.execute('CREATE VIEW src.calendar AS SELECT trade_date FROM frozen.calendar')
    db.read_parquet(str(recovery)).create_view('recovered_refs')
    db.read_parquet(str(source/'pg_stock_daily.parquet')).create_view('pg_daily')
    db.register('lifecycle',pa.Table.from_pylist(
        [{'stock_code':r['stock_code'],'status':r['status']} for r in lifecycle['rows']],
        schema=pa.schema([('stock_code',pa.string()),('status',pa.string())])))
    for name,key in [('src.stock_daily','stock_code,trade_date'),('src.calendar','trade_date'),
                     ('recovered_refs','stock_code,trade_date'),('pg_daily','id'),('lifecycle','stock_code')]:
        if db.execute(f'SELECT count(*)-count(DISTINCT ({key})) FROM {name}').fetchone()[0]:
            raise ValueError('Duplicate input keys: '+name)
    db.execute('CREATE TEMP TABLE cal AS SELECT trade_date,lag(trade_date) OVER(ORDER BY trade_date) prev FROM src.calendar')
    return db


def profiles(db):
    result={}
    for day,code,reason,source,basis,conflict in db.execute('''SELECT trade_date,stock_code,price_reason,
        price_source,reference_basis,price_conflict FROM evaluated WHERE in_scope ORDER BY trade_date,stock_code''').fetchall():
        p=result.setdefault(str(day),dict(observed={},valid={},critical_price_conflicts=0,sources=Counter()))
        p['observed'][code]=reason
        if reason=='valid': p['valid'][code]=(source,basis)
        p['critical_price_conflicts']+=int(bool(conflict))
        p['sources'][(source,basis,reason)]+=1
    return result


def source_summary(p):
    return None if p is None else [dict(price_source=k[0],reference_basis=k[1],price_reason=k[2],count=v)
        for k,v in sorted(p['sources'].items(),key=lambda item:str(item[0]))]


def build(root=DEFAULT_ROOT):
    root=Path(root).resolve()
    source,um=snapshot(root/'research/unified')
    breadth,bs=checked_release(root/'statistics/market_breadth')
    limits,ls=checked_release(root/'statistics/limit_structure')
    bi=json.loads((breadth/'inputs.json').read_text());li=json.loads((limits/'inputs.json').read_text())
    if bi['source_snapshot']!=str(source) or li['source_snapshot']!=str(source) or li['breadth_snapshot']!=str(breadth):
        raise ValueError('Input release lineage mismatch')
    if (bi['source_manifest_sha256']!=sha(manifest_path(root/'research/unified'))
        or li['source_manifest_sha256']!=bi['source_manifest_sha256']):
        raise ValueError('Source manifest differs from frozen E1/E2 lineage')
    if bs['version']!='observed-market-breadth-v1' or ls['version']!='limit-structure-v1':
        raise ValueError('Unsupported input rule version')
    for name,digest in bi['code_sha256'].items():
        if sha(REPO/name)!=digest: raise ValueError('E1 computation changed: '+name)
    for name,digest in um['files'].items():
        if sha(source/name)!=digest: raise ValueError('Unified input hash mismatch: '+name)
    recovery=Path(bi['history_snapshot'])/'recovered_references.parquet'
    lifecycle_path=Path(bi['lifecycle_report'])
    if sha(recovery)!=bi['recovered_references_sha256'] or sha(lifecycle_path)!=bi['lifecycle_sha256']:
        raise ValueError('Reference/lifecycle evidence changed')
    lifecycle=json.loads(lifecycle_path.read_text())
    frozen_files={str(p):sha(p) for base in (breadth,limits) for p in base.iterdir() if p.is_file()}
    inputs=dict(spec='v1.3',spec_edited_at='2026-11-10T11:29:54.497Z',
        source_snapshot=str(source),breadth_snapshot=str(breadth),limit_snapshot=str(limits),
        source_manifest_sha256=sha(manifest_path(root/'research/unified')),
        frozen_files=frozen_files,recovery_sha256=sha(recovery),lifecycle_sha256=sha(lifecycle_path),
        rule_version=RULE_VERSION,policy=POLICY,price_fields=list(PRICE_FIELDS),
        code_sha256={str(p.relative_to(REPO)):sha(p) for p in
            (Path(__file__).resolve(),REPO/'backend/app/analytics/direction_signals.py',REPO/'scripts/build_market_breadth.py',
             REPO/'backend/app/statistics/market_breadth.py',REPO/'backend/app/statistics/reference_recovery.py')})
    key=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()[:20]
    target=root/'statistics/direction_signals';target.mkdir(parents=True,exist_ok=True)
    out=target/'snapshots'/key;out.parent.mkdir(exist_ok=True)
    db=open_profiles(source,recovery,lifecycle)
    try:
        e1={str(r['trade_date']):r for r in rows(db,'SELECT * FROM read_parquet(?) ORDER BY trade_date',
            [[str(p) for p in sorted(breadth.glob('*.parquet'))]])}
        e2rows=json.loads((limits/'daily.json').read_text());e2={r['trade_date']:r for r in e2rows}
        if len(e2)!=len(e2rows): raise ValueError('Duplicate E2 dates')
        calendar={str(r['trade_date']):str(r['prev']) if r['prev'] else None for r in rows(db,'SELECT * FROM cal')}
        if not set(e1)<=set(calendar): raise ValueError('E1 dates outside calendar')
        snapshots=dict(unified=source.name,breadth=breadth.name,limits=limits.name)
        counts=Counter();reasons=Counter();examples={};previous_profile=None;previous_profile_day=None
        with tempfile.TemporaryDirectory(prefix='.e3-',dir=target) as temp:
            stage=Path(temp);save(stage/'inputs.json',inputs);save(stage/'rules.json',dict(version=RULE_VERSION,**POLICY))
            # Preserve every unresolved item; this annotation cannot filter the price sample.
            save(stage/'lifecycle_quality.json',json.loads((breadth/'lifecycle_quality.json').read_text()))
            for month in sorted({d[:7] for d in e1}):
                days=sorted(d for d in e1 if d.startswith(month))
                recomputed,_=calculate(db,days[0],days[-1]);pp=profiles(db)
                if len(recomputed)!=len(days): raise ValueError('Calendar partition incomplete')
                for row in recomputed:
                    if row!=e1[str(row['trade_date'])]: raise ValueError('E1 re-derived profile aggregates differ: '+str(row['trade_date']))
                output=[]
                for day in days:
                    prev=calendar[day];current=pp.get(day)
                    old=pp.get(prev) or (previous_profile if previous_profile_day==prev else None)
                    comparison=breadth_comparison(old,current)
                    record=daily_signals(day,prev,e1[day],e1.get(prev),e2.get(day),e2.get(prev),comparison,
                        snapshot_id=key,input_snapshots=snapshots,
                        source_profiles=dict(previous=source_summary(old),current=source_summary(current)))
                    output.append(record)
                    for sig in record['signals']:
                        counts[sig['layer']+'.'+sig['direction']]+=1
                        reasons.update(sig['reason_codes'])
                        if sig['layer']=='observed_breadth' and sig['direction']!='unknown':
                            examples.setdefault(sig['direction'],record)
                    if record['counts']['formal']['positive'] and record['counts']['formal']['negative']:
                        examples.setdefault('formal_mixed',record)
                    if day in ('2017-01-03','2026-11-09','2026-11-10'): examples[day]=record
                    previous_profile=current;previous_profile_day=day
                save(stage/(month+'.json'),output)
                print(month,flush=True)
            save(stage/'samples.json',examples)
            for p,digest in frozen_files.items():
                if sha(p)!=digest: raise ValueError('E1/E2 changed during build')
            files={p.name:sha(p) for p in sorted(stage.iterdir())}
            summary=dict(snapshot_id=key,rule_version=RULE_VERSION,days=len(e1),signal_counts=dict(counts),
                reason_counts=dict(reasons),coverage_ratio=None,universe_status='unknown',
                lifecycle_counts=lifecycle['counts'],files=files,
                historical_policy='frozen EOD descriptive research; not point-in-time verified')
            save(stage/'summary.json',summary)
            if out.exists():
                if {p.name:sha(p) for p in out.iterdir()}!={p.name:sha(p) for p in stage.iterdir()}:
                    raise ValueError('Deterministic rebuild mismatch')
            else:
                # Same filesystem; install an entirely new release without altering input snapshots.
                stage.rename(out)
            save(manifest_path(target),dict(dataset=str(out.relative_to(target)),version=RULE_VERSION,
                                            summary_sha256=sha(out/'summary.json')))
            return dict(snapshot=str(out),days=len(e1),signal_counts=dict(counts))
    finally: db.close()


if __name__=='__main__':
    print(json.dumps(build(),ensure_ascii=False))
