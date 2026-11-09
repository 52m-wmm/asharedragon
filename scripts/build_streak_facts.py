"""Publish independent observed facts from existing v0.5.4 evidence. No market downloads."""
from collections import Counter
from datetime import date,datetime,timezone
from pathlib import Path
import hashlib,json
import pyarrow.parquet as pq
from app.research.queries import Research
from app.research.trading_evidence import limit_evidence,streak_fields,VERSION

BASE=Path.home()/'Datasets/asharedragon/research'
ROOT=BASE/'dragon_facts'
RULE_URL='https://www.sse.com.cn/lawandrules/sselawsrules2025/repeal/rules/c/10824490/files/dcbe58edb194451d93f19b1f7dd8fb4c.docx'


def save(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,default=str,indent=2));tmp.replace(path)


def load_snapshot(root):
    m=json.loads((root/'manifest.json').read_text());p=(root/m['dataset']).resolve()
    if not p.is_relative_to(root.resolve()):raise ValueError('Snapshot escaped root')
    for name,sha in m['files'].items():
        if hashlib.sha256((p/name).read_bytes()).hexdigest()!=sha:raise ValueError('Evidence snapshot hash mismatch')
    return p,m


def build():
    prior,manifest=load_snapshot(BASE/'dragon_v054')
    now=datetime.now(timezone.utc);out=ROOT/'snapshots'/now.strftime('%Y%m%dT%H%M%S%fZ');out.mkdir(parents=True)
    cases=json.loads((prior/'cases.json').read_text())
    facts={(x['stock_code'],x['trade_date']):x for x in pq.read_table(prior/'stock_day_evidence.parquet').to_pylist()}
    source_runs=json.loads((prior/'audit_693_runs.json').read_text())
    rule_file=ROOT/'rules/sse_2023.docx'
    rule_sha=hashlib.sha256(rule_file.read_bytes()).hexdigest()
    if rule_sha!='7aa2319f6dcf597be1e86b3b69d7c2ad0e6acb2a5d0cc6be48a01af602fded40':raise ValueError('Reviewed rule document changed')
    rule=dict(id='sse-2023-daily-upper-bound:'+rule_sha,url=RULE_URL,sha256=rule_sha,reviewed_at_utc=now.isoformat(),
        reviewed_scope='SH main-board A shares, 2023-04-10 through 2024-12-31 only',
        premise='Daily cap applies to session trades; no-limit sessions have no daily closing limit-up. Intraday order bands are not daily limit-up.',
        sections=['3.3.13','3.3.14','3.3.15'])
    converted=[]
    with Research() as r:
        calendar=[x['trade_date'] for x in r.query('select trade_date from calendar order by trade_date')];idx={d:i for i,d in enumerate(calendar)}
        # Five bounded windows only; no whole-market rescan and no price writes.
        for case in cases:
            code=case['stock_code'];start=date.fromisoformat(case['window_start']);end=date.fromisoformat(case['window_end'])
            rows=r.query('select * from daily where stock_code=? and trade_date between ? and ? order by trade_date',[code,start,end])
            for bar in rows:
                d=bar['trade_date'];f=facts[code,d]
                scoped=code.startswith('60') and code.endswith('.SH') and date(2023,4,10)<=d<=date(2024,12,31)
                if not scoped or f['status']!='unknown' or f['limit']['quality']=='conflict' or not f['security_id']:continue
                ev={'daily_upper_bound_or_no_limit':True,'rule_evidence_id':rule['id'],'rule_reviewed_at_utc':now.isoformat()}
                proof=limit_evidence(bar,ev,f['trading'])
                if proof['status']=='confirmed_not_limit_up':
                    proof['evidence_ids']=sorted(set(proof['evidence_ids']+f['trading']['source_ids']))
                    f.update(status=proof['status'],limit=proof,evidence_ids=proof['evidence_ids'],known_at_utc=proof['known_at_utc'])
                    converted.append(dict(stock_code=code,trade_date=d,close=bar['close'],high=bar['high'],basis=proof['basis']))
            span=calendar[idx[start]:idx[end]+1];byday={d:facts.get((code,d),{}) for d in span}
            case['runs']=streak_fields(code,span,byday);case['rows']=list(byday.values())
            case['gate_passed']=bool(case['runs']) and all(x['exact_cycle_eligible'] for x in case['runs'])
            case['limit_counts']=dict(Counter(f.get('status','unknown') for f in byday.values()))
            case['unconfirmed_dates']=[dict(trade_date=d,reason=f.get('limit',{}).get('rule_reason','daily_missing'),
                missing=['direct_closing_event_or_independent_limit_reference'],
                st_required_for_direct_event=False) for d,f in byday.items() if f.get('status')=='unknown']
    # Preserve source-cohort identity and deduplicate identical confirmed segments.
    runs={}
    for group,source in [('audit_693',source_runs),('priority_cases',[x for c in cases for x in c['runs']])]:
        for run in source:
            key=(run['stock_code'],str(run['first_observed_date']),str(run['last_observed_date']))
            runs[key]=(group,run)
    published=[]
    for (code,start,last),(group,run) in runs.items():
        if run['observed_length']<3:continue
        fields=run['fields'];a=date.fromisoformat(start);b=date.fromisoformat(last)
        left=calendar[idx[a]-1] if idx[a]>0 else None;right=calendar[idx[b]+1] if idx[b]+1<len(calendar) else None
        blockers=[]
        for side,d,field in [('left',left,'first_board_date'),('right',right,'cycle_end_date')]:
            if fields[field]['value'] is not None:continue
            f=facts.get((code,d),{});state=f.get('status','unknown')
            blockers.append(dict(field=field,stock_code=code,trade_date=d,reason=side+'_boundary_'+state,
                detail=f.get('limit',{}).get('rule_reason','no_evidence_in_audit_window'),
                minimal_evidence='该交易日可信收盘状态；若仍涨停则向外延伸至首个确认非涨停日',
                alternatives=['direct_closing_event','trusted_official_limit_and_raw_close','reviewed_daily_bound_and_close_below_high'],
                st_required_for_direct_event=False))
        if fields['exact_max_streak']['value'] is None:
            blockers.append(dict(field='exact_max_streak',reason='requires_both_verified_boundaries',
                dates=[x['trade_date'] for x in blockers],minimal_evidence='补齐上述边界，保留中间全部连续交易日证据'))
        published.append(dict(fact_id='observed:'+code+':'+start+':'+last,stock_code=code,cohort=group,
            first_observed_date=start,last_observed_date=last,observed_min_height=run['observed_length'],
            exact_max_streak=fields['exact_max_streak']['value'],first_board_date=fields['first_board_date']['value'],
            cycle_end_date=fields['cycle_end_date']['value'],fields=fields,progress=run['progress'],
            complete_cycle_eligible=run['exact_cycle_eligible'],
            evidence_status='confirmed_complete_cycle' if run['exact_cycle_eligible'] else 'confirmed_lower_bound',
            blockers=blockers,label_version=VERSION,
            evidence_ids=sorted({eid for x in fields.values() for eid in x['evidence_ids']}),
            publication_blockers=['whole_market_interval_acceptance_not_passed'],
            retrospective=True))
    published.sort(key=lambda x:(-x['observed_min_height'],x['stock_code'],x['first_observed_date']))
    counts={str(n):sum(x['fields'][f'observed_streak_ge_{n}']['value'] is True for x in published) for n in (3,5,7,10)}
    summary=dict(version=VERSION,built_at_utc=now,source_snapshot=str(prior),source_manifest=manifest,
        observed_segments=len(published),threshold_counts=counts,threshold_fact_count=sum(counts.values()),
        exact_cycles=sum(x['complete_cycle_eligible'] for x in published),lower_bound_only=sum(not x['complete_cycle_eligible'] for x in published),
        confirmed_negative_days=converted,case_audits=[{k:v for k,v in c.items() if k not in ('rows','runs')} for c in cases],
        formal_statistics_eligible=False,formal_whole_market_L1_labels=0,
        scope='Five priority cases plus original 693-cycle audit; not whole-market coverage',rule_evidence=rule)
    save(out/'facts.json',published);save(out/'cases.json',cases);save(out/'summary.json',summary)
    pq.write_table(__import__('pyarrow').Table.from_pylist(list(facts.values())),out/'stock_day_evidence.parquet',compression='zstd')
    save(ROOT/'manifest.json',dict(dataset=str(out.relative_to(ROOT)),version=VERSION,
        files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()}))
    print(json.dumps({k:summary[k] for k in ('observed_segments','threshold_counts','threshold_fact_count','exact_cycles','lower_bound_only','confirmed_negative_days')},default=str,ensure_ascii=False,indent=2))

if __name__=='__main__':build()
