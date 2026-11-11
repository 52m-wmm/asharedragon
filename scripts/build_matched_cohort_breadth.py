"""D0/D1 independent release: full old/new audit and paired-price cohort evidence."""
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq

from app.analytics.matched_cohort_breadth import VERSION, POLICY, diagnose, matched_breadth, old_primary, valid_codes
from app.analytics.direction_signals import breadth_comparison, breadth_quality
from app.research.queries import DEFAULT_ROOT
from scripts.build_direction_signals import checked_release, open_profiles, profiles, source_summary
from scripts.build_market_breadth import manifest_path, REPO, calculate, rows, save, sha, snapshot


def detailed_profiles(db,source,rule):
    fields=['trade_date','stock_code','price_reason','price_source','reference_basis','daily_return',
            'price_conflict','identity_status','trade_status','close','reference_price']
    result={}
    for row in rows(db,'SELECT '+','.join(fields)+' FROM evaluated WHERE in_scope ORDER BY trade_date,stock_code'):
        day=str(row.pop('trade_date'));code=row.pop('stock_code')
        p=result.setdefault(day,dict(trade_date=day,source_snapshot=source,breadth_rule_version=rule,
                                     scope=POLICY['scope'],stocks={}))
        p['stocks'][code]=row
    return result


def new_primary(result):
    if result['calculable']: return result['status']
    reasons=result['reason_codes']
    if any(r in reasons for r in ('previous_session_profile_missing','current_session_profile_missing','zero_valid_sample','empty_common_sample')):
        return 'missing_usable_comparison'
    if any('source' in r or 'reference' in r for r in reasons): return 'source_or_reference_untrusted'
    return 'other_gate'


def build(root=DEFAULT_ROOT):
    root=Path(root).resolve();source,um=snapshot(root/'research/unified')
    breadth,bs=checked_release(root/'statistics/market_breadth')
    e2,_=checked_release(root/'statistics/limit_structure')
    old,os=checked_release(root/'statistics/direction_signals')
    oi=json.loads((old/'inputs.json').read_text());bi=json.loads((breadth/'inputs.json').read_text())
    if (oi['source_snapshot']!=str(source) or oi['breadth_snapshot']!=str(breadth) or oi['limit_snapshot']!=str(e2)
        or bi['source_snapshot']!=str(source) or bs['version']!='observed-market-breadth-v1'
        or os['rule_version']!='direction-signals-v1'): raise ValueError('Frozen release lineage/version mismatch')
    for name,digest in {**bi['code_sha256'],**oi['code_sha256']}.items():
        if sha(REPO/name)!=digest: raise ValueError('Preserved implementation changed: '+name)
    if sha(manifest_path(root/'research/unified'))!=bi['source_manifest_sha256']:raise ValueError('Source manifest changed')
    for name,digest in um['files'].items():
        if sha(source/name)!=digest:raise ValueError('Unified source changed: '+name)
    lifecycle_path=Path(bi['lifecycle_report']);recovery=Path(bi['history_snapshot'])/'recovered_references.parquet'
    if sha(lifecycle_path)!=bi['lifecycle_sha256'] or sha(recovery)!=bi['recovered_references_sha256']:
        raise ValueError('Reference or lifecycle evidence changed')
    frozen={str(p):sha(p) for base in (breadth,e2,old) for p in base.rglob('*') if p.is_file()}
    for folder in ['research/unified','statistics/history_limits','statistics/market_breadth','statistics/limit_structure','statistics/direction_signals']:
        p=manifest_path(root/folder);frozen[str(p)]=sha(p)
    inputs=dict(spec='v1.5 section 13',spec_edited_at='2026-11-10T11:55:22.147Z',rule_version=VERSION,
        source_snapshot=str(source),breadth_snapshot=str(breadth),e2_snapshot=str(e2),old_e3_snapshot=str(old),
        frozen_inputs=frozen,policy=POLICY,reference_recovery_sha256=sha(recovery),lifecycle_sha256=sha(lifecycle_path),
        code_sha256={str(p.relative_to(REPO)):sha(p) for p in
            [Path(__file__).resolve(),REPO/'backend/app/analytics/matched_cohort_breadth.py']},
        reused_code_sha256={**bi['code_sha256'],**oi['code_sha256']})
    key=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()[:20]
    target=root/'statistics/matched_cohort_breadth';target.mkdir(parents=True,exist_ok=True)
    out=target/'snapshots'/key;out.parent.mkdir(exist_ok=True)
    lifecycle=json.loads(lifecycle_path.read_text());db=open_profiles(source,recovery,lifecycle)
    try:
        e1={str(r['trade_date']):r for r in rows(db,'SELECT * FROM read_parquet(?) ORDER BY trade_date',
            [[str(p) for p in sorted(breadth.glob('*.parquet'))]])}
        calendar={str(r['trade_date']):str(r['prev']) if r['prev'] else None for r in rows(db,'SELECT * FROM cal')}
        total=Counter();old_reasons=Counter();old_main=Counter();new_reasons=Counter();new_main=Counter()
        years=defaultdict(Counter);periods=defaultdict(Counter);samples={};diff=[];old_combos=Counter();quality_pairs=Counter()
        last=None;last_old_profile=None;last_day=None
        with tempfile.TemporaryDirectory(prefix='.d01-',dir=target) as temp:
            stage=Path(temp);(stage/'cohorts').mkdir()
            save(stage/'inputs.json',inputs);save(stage/'rules.json',dict(rule_version=VERSION,**POLICY))
            save(stage/'lifecycle_quality.json',json.loads((breadth/'lifecycle_quality.json').read_text()))
            for month in sorted({d[:7] for d in e1}):
                days=sorted(d for d in e1 if d.startswith(month));recomputed,_=calculate(db,days[0],days[-1])
                if len(recomputed)!=len(days) or any(r!=e1[str(r['trade_date'])] for r in recomputed):
                    raise ValueError('E1 aggregate mismatch')
                simple=profiles(db);detailed=detailed_profiles(db,source.name,bs['version'])
                old_records={r['trade_date']:r for r in json.loads((old/(month+'.json')).read_text())}
                output=[];members=[]
                for day in days:
                    prev=calendar[day];a=detailed.get(prev) or (last if last_day==prev else None);b=detailed.get(day)
                    previous_simple=simple.get(prev) or (last_old_profile if last_day==prev else None);pb=simple.get(day)
                    original=old_records[day];old_signals=[s for s in original['signals'] if s['layer']=='observed_breadth']
                    # Independently reproduce v1's date-level failure reasons without changing v1.
                    reasons=set(breadth_comparison(previous_simple,pb)['reasons']+breadth_quality(e1.get(prev))+breadth_quality(e1[day]))
                    if any(s['value'] is None or s['previous_value'] is None for s in old_signals):reasons.add('value_missing_or_nonfinite')
                    original_reasons=set(r for s in old_signals for r in s['reason_codes'])
                    if reasons!=original_reasons:raise ValueError('Old reason reproduction differs: '+day)
                    old_ok=all(s['direction']!='unknown' for s in old_signals)
                    audit=diagnose(a,b)
                    audit.update(old_reason_codes=sorted(reasons),old_primary_reason=old_primary(reasons),
                        source_profiles=dict(previous=source_summary(previous_simple),current=source_summary(pb)))
                    result=matched_breadth(day,prev,a,b,snapshot_id=key,
                        input_snapshots=dict(unified=source.name,breadth=breadth.name,old_e3=old.name,strict_e2=e2.name))
                    common=sorted(valid_codes(a)&valid_codes(b))
                    for c in common:
                        members.append(dict(trade_date=day,stock_code=c,previous_return=a['stocks'][c]['daily_return'],
                            current_return=b['stocks'][c]['daily_return']))
                    result['cohort_evidence']=f'cohorts/{month}.parquet; trade_date={day}'
                    record=dict(trade_date=day,previous_trading_day=prev,snapshot_id=key,rule_version=VERSION,
                        observable_breadth=dict(previous=e1.get(prev),current=e1[day]),matched_cohort_breadth=result,
                        non_st_verified=dict(source_snapshot=e2.name,policy='unchanged E2/E3 strict eligibility; never inferred from common-price cohort'),
                        preserved_limit_signals=[s for s in original['signals'] if s['layer']!='observed_breadth'],
                        old_breadth_signals=old_signals,audit=audit)
                    output.append(record)
                    measures=dict(days=1,old_calculable=int(old_ok),new_calculable=int(result['calculable']),
                        newly_calculable=int(result['calculable'] and not old_ok),
                        lost_calculable=int(old_ok and not result['calculable']),
                        comparable=int(result['status']=='comparable'),mixed=int(result['status']=='mixed'),
                        composition_sensitive=int(result['status']=='composition_sensitive'),unknown=int(not result['calculable']))
                    total.update(measures);years[day[:4]].update(measures)
                    sources=Counter(r['price_source'] for r in (b or {}).get('stocks',{}).values())
                    dominant=sources.most_common(1)[0][0] if sources else 'none'
                    period='purchased' if dominant and dominant.startswith('purchased_') else 'extension' if dominant and 'unadjusted' in dominant else 'recent'
                    periods[period].update(measures)
                    old_reasons.update(reasons);old_main[old_primary(reasons)]+=1;old_combos[tuple(sorted(reasons))]+=1
                    new_main[new_primary(result)]+=1
                    if not result['calculable']:new_reasons.update(result['reason_codes'])
                    for item in audit['quality_transitions']:quality_pairs[(item['previous'],item['current'])]+=item['count']
                    diff.append(dict(trade_date=day,previous_trading_day=prev,source_period=period,
                        old_calculable=old_ok,new_calculable=result['calculable'],new_status=result['status'],
                        common_count=result['common_count'],common_previous_ratio=result['common_previous_ratio'],
                        common_current_ratio=result['common_current_ratio'],delta_pp=result['delta'],
                        old_primary=old_primary(reasons),old_reason_codes='|'.join(sorted(reasons)),
                        new_reason_codes='|'.join(result['reason_codes'])))
                    if day in ('2017-01-03','2017-01-04','2026-03-16','2026-11-09','2026-11-10'):
                        samples[day]=record
                    if result['calculable']:
                        samples.setdefault('year_'+day[:4],record)
                        if day[:7]!=prev[:7]:samples.setdefault('cross_month',record)
                        if day[:4]!=prev[:4]:samples.setdefault('cross_year',record)
                        if audit['category_counts'].get('observed_suspension_evidence'):samples.setdefault('suspension',record)
                        if audit['category_counts'].get('price_quality_issue'):samples.setdefault('price_quality_change',record)
                        samples.setdefault(result['status'],record)
                    last=b;last_old_profile=pb;last_day=day
                save(stage/(month+'.json'),output)
                schema=pa.schema([('trade_date',pa.string()),('stock_code',pa.string()),('previous_return',pa.float64()),('current_return',pa.float64())])
                pq.write_table(pa.Table.from_pylist(members,schema=schema),stage/'cohorts'/(month+'.parquet'),compression='zstd')
                print(month,flush=True)
            save(stage/'samples.json',samples)
            with (stage/'old_new_diff.csv').open('w',newline='') as handle:
                writer=csv.DictWriter(handle,fieldnames=list(diff[0]));writer.writeheader();writer.writerows(diff)
            for p,digest in frozen.items():
                if sha(p)!=digest:raise ValueError('Protected input changed: '+p)
            summary=dict(snapshot_id=key,rule_version=VERSION,totals=dict(total),by_year=dict(years),by_source_period=dict(periods),
                old_reason_dates=dict(old_reasons),old_primary_dates=dict(old_main),new_unknown_reason_dates=dict(new_reasons),
                new_primary_dates=dict(new_main),old_reason_combinations=[dict(reasons=list(k),days=v) for k,v in sorted(old_combos.items())],
                price_quality_transitions=[dict(previous=k[0],current=k[1],stock_pairs=v) for k,v in sorted(quality_pairs.items())],
                lifecycle_conflicts=lifecycle['counts']['conflict'],coverage_ratio=None,
                files={str(p.relative_to(stage)):sha(p) for p in sorted(stage.rglob('*')) if p.is_file()})
            save(stage/'summary.json',summary)
            if out.exists():
                if {str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file()}!={str(p.relative_to(stage)):sha(p) for p in stage.rglob('*') if p.is_file()}:
                    raise ValueError('Deterministic rebuild mismatch')
            else:stage.rename(out)
            save(manifest_path(target),dict(version=VERSION,dataset=str(out.relative_to(target)),summary_sha256=sha(out/'summary.json')))
            return dict(snapshot=str(out),totals=dict(total),new_unknown_reason_dates=dict(new_reasons))
    finally:db.close()


if __name__=='__main__':
    print(json.dumps(build(),ensure_ascii=False))
