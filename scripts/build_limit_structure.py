"""E2 only: immutable local supplier/strict evidence, no API or signal changes.

Run from the repository root with python -m scripts.build_limit_structure.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pyarrow as pa

from app.analytics.limit_structure import VERSION, pool, promotion, structure
from app.research.queries import DEFAULT_ROOT
from scripts.build_market_breadth import manifest_path, snapshot, sha, rows, save, open_inputs, calculate, REPO


def build(root=DEFAULT_ROOT, provider='akshare'):
    # The existing coverage metadata certifies only AKShare partitions. Do not mix
    # Tushare events into that denominator or infer completeness from event presence.
    if provider!='akshare': raise ValueError('Only audited AKShare coverage is supported in E2')
    root=Path(root).resolve()
    source,source_manifest=snapshot(root/'research/unified')
    history,_=snapshot(root/'statistics/history_limits')
    breadth,breadth_manifest=snapshot(root/'statistics/market_breadth')
    breadth_inputs=json.loads((breadth/'inputs.json').read_text())
    history_summary=json.loads((history/'summary.json').read_text())
    if any(Path(p).resolve()!=source for p in [breadth_inputs['source_snapshot'],history_summary['source_snapshot']]):
        raise ValueError('E1/E2 input snapshots differ')
    if Path(breadth_inputs['history_snapshot']).resolve()!=history:
        raise ValueError('E1 reference recovery is from another history snapshot')
    if sha(breadth/'summary.json')!=breadth_manifest['summary_sha256']:
        raise ValueError('E1 summary hash mismatch')
    for name,expected in source_manifest['files'].items():
        path=(source/name).resolve()
        if not path.is_relative_to(source) or sha(path)!=expected: raise ValueError('Unified input hash mismatch')
    recovery=history/'recovered_references.parquet'
    if sha(recovery)!=breadth_inputs['recovered_references_sha256']: raise ValueError('Recovery input changed')
    lifecycle_path=Path(breadth_inputs['lifecycle_report'])
    if sha(lifecycle_path)!=breadth_inputs['lifecycle_sha256']: raise ValueError('Lifecycle evidence changed')
    lifecycle=json.loads(lifecycle_path.read_text())
    code_paths=['scripts/build_limit_structure.py','backend/app/analytics/limit_structure.py',
                'backend/app/analytics/metrics.py','scripts/build_market_breadth.py',
                'backend/app/statistics/market_breadth.py']
    inputs=dict(version=VERSION,provider=provider,source_snapshot=str(source),history_snapshot=str(history),
                breadth_snapshot=str(breadth),source_manifest_sha256=sha(manifest_path(root/'research/unified')),
                history_daily_sha256=sha(history/'daily.parquet'),history_positives_sha256=sha(history/'positives.parquet'),
                history_days_sha256=sha(history/'days.parquet'),recovery_sha256=sha(recovery),
                lifecycle_sha256=sha(lifecycle_path),code_sha256={p:sha(REPO/p) for p in code_paths})
    key=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()[:20]
    target=root/'statistics/limit_structure';out=target/'snapshots'/key
    db=open_inputs(source,recovery,lifecycle)
    try:
        calendar=[r[0] for r in db.execute('SELECT trade_date FROM cal ORDER BY trade_date').fetchall()]
        end=json.loads((source/'summary.json').read_text())['data_end']
        previous={day:calendar[i-1] if i else None for i,day in enumerate(calendar)}
        following={calendar[i]:calendar[i+1] for i in range(len(calendar)-1)}
        calendar=[d for d in calendar if '2017-01-01'<=str(d)<=end]
        events=rows(db,'SELECT * FROM src.events WHERE provider=? ORDER BY trade_date,limit_type,stock_code',[provider])
        coverages=rows(db,'SELECT * FROM read_parquet(?) WHERE provider=? AND dataset IN (\'up\',\'broken\',\'down\')',
                       [str(source/'pg_data_coverage.parquet'),provider])
        coverage={(r['trade_date'],r['dataset']):r for r in coverages}
        if len(coverage)!=len(coverages): raise ValueError('Duplicate coverage keys')
        by_pool=defaultdict(list);targets=set()
        for e in events:
            by_pool[e['trade_date'],e['limit_type']].append(e)
            targets.add((e['stock_code'],e['trade_date']))
            if e['limit_type']=='up' and e['trade_date'] in following:
                targets.add((e['stock_code'],following[e['trade_date']]))
        db.register('target_keys',pa.Table.from_pylist([dict(stock_code=c,trade_date=d) for c,d in sorted(targets)],
                    schema=pa.schema([('stock_code',pa.string()),('trade_date',pa.date32())])))
        db.read_parquet(str(history/'positives.parquet')).create_view('historical_positives')
        db.read_parquet(str(history/'days.parquet')).create_view('historical_days')
        facts=defaultdict(dict)
        # Reuse E1 price validity; only months containing a target stock/date are needed.
        for month in sorted({str(day)[:7] for _,day in targets}):
            days=[d for d in calendar if str(d).startswith(month)]
            if not days: continue
            calculate(db,days[0],days[-1])
            selected=rows(db,"""SELECT e.stock_code,e.trade_date,e.identity_status,e.price_reason,e.trade_status,
              e.event_conflict,e.is_limit_up,e.event_raw_id,e.event_source,e.reference_basis,p.first_board_date,
              (h.status='confirmed' AND e.event_source=?) rule_confirmed,
              coalesce(p.exact_start AND p.first_board_date IS NOT NULL AND p.chain_status='confirmed'
                AND p.height=p.vendor_height AND p.confirmed_height=p.vendor_height
                AND boundary.up IS FALSE AND boundary.status='confirmed' AND boundary.event_source=e.event_source,false) height_confirmed
              FROM evaluated e JOIN target_keys k USING(stock_code,trade_date)
              LEFT JOIN historical_positives p USING(stock_code,trade_date)
              LEFT JOIN historical_days h USING(stock_code,trade_date)
              LEFT JOIN cal start_calendar ON start_calendar.trade_date=p.first_board_date
              LEFT JOIN historical_days boundary ON boundary.stock_code=e.stock_code
                AND boundary.trade_date=start_calendar.prev""",[provider])
            for f in selected: facts[f['trade_date']][f['stock_code']]=f
        historic={r['trade_date']:r for r in rows(db,"""SELECT trade_date,confirmed AS event_confirmed_observed,
          reconstructed_candidate,unknown_observed FROM read_parquet(?) ORDER BY trade_date""",[str(history/'daily.parquet')])}
        pools={}
        def get_pool(day,kind):
            pair=(day,kind)
            if pair not in pools:
                pools[pair]=pool(provider,day,kind,by_pool[pair],coverage.get(pair),scope_verified=False)
            return pools[pair]
        result=[]
        for day in calendar:
            prev=previous[day];up=get_pool(day,'up')
            item=dict(trade_date=day,previous_trading_day=prev,provider=provider,snapshot_id=key,
                      source_snapshot=source.name,rule_version=VERSION,
                      historical_evidence=historic.get(day),coverage_ratio=None,universe_status='unknown')
            item.update(structure(up,get_pool(day,'broken'),get_pool(day,'down'),facts[day]))
            for level,name in [(1,'promotion_1to2'),(2,'promotion_2to3')]:
                item[name]=promotion(get_pool(prev,'up'),up,facts[prev],facts[day],level,prev)
            result.append(item)
        lifecycle_quality=dict(counts=lifecycle['counts'],
            unresolved=[r for r in lifecycle['rows'] if r['status']!='verified'],
            policy='retain all conflicts; no lifecycle filtering or denominator adjustment')
        payloads={'daily.json':result,'inputs.json':inputs,'lifecycle_quality.json':lifecycle_quality}
        # Stable payloads; a rerun verifies rather than overwrites an existing release.
        if out.exists():
            for name,payload in payloads.items():
                expected=json.loads(json.dumps(payload,default=str))
                if json.loads((out/name).read_text())!=expected: raise ValueError('E2 snapshot differs on rerun')
        else:
            import tempfile
            target.mkdir(parents=True,exist_ok=True)
            (target/'snapshots').mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='building-',dir=target) as temporary:
                temp=Path(temporary)
                for name,payload in payloads.items(): save(temp/name,payload)
                save(temp/'summary.json',dict(version=VERSION,snapshot_id=key,days=len(result),
                    computed_at_utc=datetime.now(timezone.utc).isoformat(),
                    scope_policy='supplier success is diagnostic; source scope and non-ST eligibility must be verified for strict metrics',
                    lifecycle_conflicts=lifecycle['counts']['conflict'],
                    files={name:sha(temp/name) for name in payloads}))
                temp.rename(out)
        summary=json.loads((out/'summary.json').read_text())
        for name,expected in summary['files'].items():
            if sha(out/name)!=expected: raise ValueError('E2 output hash mismatch')
        save(manifest_path(target),dict(version=VERSION,dataset=str(out.relative_to(target)),summary_sha256=sha(out/'summary.json')))
        return dict(snapshot=str(out),days=len(result),latest=result[-1])
    finally:
        db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    args=parser.parse_args()
    result=build(args.root)
    print(json.dumps({k:v for k,v in result.items() if k!='latest'},default=str))
    latest=result['latest']
    print(json.dumps(dict(trade_date=latest['trade_date'],supplier=latest['supplier_diagnostic'],
        strict=latest['strict_non_st'],promotion_1to2=latest['promotion_1to2']['supplier_diagnostic'],
        promotion_2to3=latest['promotion_2to3']['supplier_diagnostic']),default=str))
