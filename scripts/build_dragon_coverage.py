"""Stream all 2017–2026 stock sessions through existing rules; never relax formal gates.

State-only observations and source event truth stay separate from price diagnostics.
Published annual artifacts are immutable and atomically selected by manifest.
"""
from collections import Counter,defaultdict
from datetime import date,datetime,timezone
from pathlib import Path
import argparse,csv,hashlib,json,shutil
from itertools import groupby
import pyarrow as pa
import pyarrow.parquet as pq
import duckdb
from app.research.queries import Research
from app.research.limit_rules import reconstruct
from app.research.historical_state import resolve_state,reference_evidence
from app.research.dragons import cycles,research_state
from build_us_market_signals import event_snapshot
from collect_historical_state import ROOT,BASE,save

VERSION='dragon-coverage-v0.5.2'
FACT_SCHEMA=pa.schema([
    ('stock_code',pa.string()),('trade_date',pa.date32()),('price_present',pa.bool_()),
    ('state',pa.string()),('state_quality',pa.string()),('is_st',pa.bool_()),('trade_status',pa.bool_()),
    ('state_evidence_ids',pa.list_(pa.string())),('source',pa.string()),
    ('reference_evidence_ids',pa.list_(pa.string())),('reference_price_used',pa.float64()),('reference_verified',pa.bool_()),
    ('status',pa.string()),('reason',pa.string()),('diagnostic_classification',pa.string()),('diagnostic_reason',pa.string()),
    ('diagnostic_limit_price',pa.float64()),('assumptions',pa.list_(pa.string())),
    ('event_evidence_ids',pa.list_(pa.string())),('independent_event',pa.string()),('event_rule_conflict',pa.bool_()),
    ('official_limit_available',pa.bool_()),('formal_eligible',pa.bool_())])


def classify(row, evidence, event_kind=None, event_conflict=False):
    strict=reconstruct(row,evidence)
    diagnostic=reconstruct(row,evidence,diagnostic=True)
    conflict=event_conflict or (event_kind is not None and diagnostic['classification']!='unknown' and (diagnostic['classification']=='up')!=(event_kind=='up'))
    if conflict:
        return strict,dict(diagnostic,classification='unknown',reason='independent_event_conflict'),'unknown',True
    # Source truth is separate. Absence from U pools is never a negative label.
    verified=(strict['formal_eligible'] and ((event_kind is not None and
              (strict['classification']=='up')==(event_kind=='up')) or
              (strict['classification']=='suspended' and evidence.get('official_suspension') is True)))
    status=('confirmed_limit_up' if strict['classification']=='up' else 'confirmed_not_limit_up') if verified else 'unknown'
    return strict,diagnostic,status,False


def build(resume=None):
    statepath=ROOT/'status_observations.parquet'
    state_summary=json.loads((ROOT/'status_summary.json').read_text())
    if state_summary['stk_limit_available'] or state_summary['stock_st_available']:
        raise ValueError('New independent state/limit cache requires explicit normalization and reconciliation')
    states=defaultdict(list)
    for r in pq.read_table(statepath).to_pylist():states[r['stock_code'],r['trade_date']].append(r)
    referencepath=ROOT/'reference_observations.parquet';references=defaultdict(list)
    for ref in pq.read_table(referencepath).to_pylist():references[ref['stock_code'],ref['trade_date']].append(ref)
    _,pools,proof,issues=event_snapshot();events=defaultdict(list)
    for (provider,day,kind),pool in pools.items():
        for row in pool['rows']:events[row['stock_code'],day].append(dict(row,kind='up' if kind=='U' else 'not_up',provider=provider,evidence_id=f"pg_raw:{pool['raw_id']}:{pool['hash']}",observed_at=pool['observed_at']))
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out=Path(resume).resolve() if resume else ROOT/'snapshots'/stamp
    if not out.resolve().is_relative_to((ROOT/'snapshots').resolve()):raise ValueError('Invalid resume directory')
    out.mkdir(parents=True,exist_ok=bool(resume))
    if resume:
        if (out/'status_observations.parquet').read_bytes()!=statepath.read_bytes():raise ValueError('State inputs changed; start a new snapshot')
        if (out/'reference_observations.parquet').read_bytes()!=referencepath.read_bytes():raise ValueError('Reference inputs changed')
        if json.loads((out/'source_events.json').read_text())!=json.loads(json.dumps(proof,default=str)):raise ValueError('Event inputs changed')
    else:
        shutil.copy2(statepath,out/'status_observations.parquet');shutil.copy2(referencepath,out/'reference_observations.parquet');save(out/'source_events.json',proof)
    with Research() as r:
        stocks={x['stock_code']:x for x in r.query('SELECT * FROM stocks')}
        calendar=[x['trade_date'] for x in r.query('SELECT trade_date FROM calendar ORDER BY trade_date')];idx={d:i for i,d in enumerate(calendar)}
        bounds=r.query('SELECT min(trade_date) lo,max(trade_date) hi FROM daily')[0]
        cal=[d for d in calendar if bounds['lo']<=d<=bounds['hi']]
        # Catalogue grid adds absent active days, while retaining every observed bar.
        r.db.execute('''CREATE TEMP VIEW expected AS SELECT s.stock_code,c.trade_date FROM stocks s CROSS JOIN calendar c
          WHERE c.trade_date BETWEEN ? AND ? AND c.trade_date>=s.listing_date AND (s.delisting_date IS NULL OR c.trade_date<=s.delisting_date)
          AND s.stock_code IN (SELECT DISTINCT stock_code FROM daily)'''.replace('BETWEEN ? AND ?',f"BETWEEN DATE '{bounds['lo']}' AND DATE '{bounds['hi']}'"))
        yearly=json.loads((out/'yearly_progress.json').read_text()) if resume and (out/'yearly_progress.json').exists() else []
        for year in range(2017,2027):
            path=out/f'facts_{year}.parquet'
            previous_year=next((y for y in yearly if y['year']==year),None)
            if previous_year:
                metadata=pq.ParquetFile(path)
                if metadata.metadata.num_rows!=previous_year['rows'] or not metadata.schema_arrow.equals(FACT_SCHEMA):raise ValueError('Incomplete resume partition')
                print(year,'resume validated',flush=True);continue
            writer=pq.ParquetWriter(path,FACT_SCHEMA,compression='zstd')
            counts=Counter();unknown=Counter();diag=Counter();days=set();known_days=set()
            query='''SELECT coalesce(p.stock_code,e.stock_code) stock_code,coalesce(p.trade_date,e.trade_date) trade_date,
                p.open,p.high,p.low,p.close,p.pre_close,p.source,p.adjustment_type
                FROM (SELECT * FROM daily WHERE year(trade_date)=?) p FULL OUTER JOIN
                (SELECT * FROM expected WHERE year(trade_date)=?) e USING(stock_code,trade_date)
                ORDER BY stock_code,trade_date'''
            reader=r.db.execute(query,[year,year]).to_arrow_reader(batch_size=16384)
            for batch in reader:
                rows=[]
                for bar in batch.to_pylist():
                    code,d=bar['stock_code'],bar['trade_date'];key=(code,d);s=stocks.get(code,{})
                    state=resolve_state(states.get(key,[]),d,s.get('listing_date'),s.get('delisting_date'))
                    working_bar,reference_verified,price_conflict,reference_ids=reference_evidence(bar if bar['close'] is not None else None,references.get(key,[]))
                    listed=s.get('listing_date');previous=calendar[idx[d]-1] if idx[d] else None
                    prior=resolve_state(states.get((code,previous),[]),previous) if previous else {}
                    ev=dict(listing_date=listed,delisting_date=s.get('delisting_date'),listing_session=idx[d]-idx[listed]+1 if listed in idx else None,
                        trade_status=state['trade_status'],is_st=state['is_st'],special_status='resumption_unverified' if prior.get('trade_status') is False and state['trade_status'] is True else 'unknown',
                        reference_verified=reference_verified,conflict=state['conflict'] or price_conflict,
                        official_suspension='official_company_suspension_notice' in state['sources'],
                        event_present=any(x['kind']=='up' for x in events.get(key,[])))
                    observed=events.get(key,[]);kinds={x['kind'] for x in observed}
                    duplicate=any(n>1 for n in Counter(x['provider'] for x in observed).values())
                    event_kind=next(iter(kinds)) if len(kinds)==1 else None
                    strict,dg,status,conflict=classify(working_bar,ev,event_kind,duplicate or len(kinds)>1)
                    row=dict(stock_code=code,trade_date=d,price_present=bar['close'] is not None,state=state['state'],state_quality=state['quality'],
                        is_st=state['is_st'],trade_status=state['trade_status'],state_evidence_ids=state['evidence_ids'],source=bar['source'],
                        reference_evidence_ids=reference_ids,reference_price_used=working_bar.get('pre_close') if working_bar else None,reference_verified=reference_verified,
                        status=status,reason='independent_event_conflict' if conflict else strict['reason'],diagnostic_classification=dg['classification'],diagnostic_reason=dg['reason'],
                        diagnostic_limit_price=float(dg['limit_price']) if dg['limit_price'] is not None else None,assumptions=dg['assumptions'],
                        event_evidence_ids=sorted({x['evidence_id'] for x in observed}),independent_event=event_kind,event_rule_conflict=conflict,
                        official_limit_available=False,formal_eligible=status!='unknown')
                    rows.append(row);counts[status]+=1;counts['rows']+=1;counts['prices']+=row['price_present'];counts['st_known']+=state['is_st'] is not None
                    diag[dg['classification']]+=1;days.add(d)
                    if dg['classification']!='unknown':known_days.add(d)
                    if status=='unknown':unknown[row['reason']]+=1
                writer.write_table(pa.Table.from_pylist(rows,schema=FACT_SCHEMA))
            writer.close()
            yearly.append(dict(year=year,**counts,audited_sessions=len(days),diagnostically_classifiable_sessions=len(known_days),diagnostic_counts=dict(diag),unknown_reasons=dict(unknown),formal_seasonality_eligible=False))
            save(out/'yearly_progress.json',yearly);print(year,dict(counts),dict(diag),flush=True)
    # One stock across ALL years per iteration: no resets at partition boundaries.
    db=duckdb.connect(config={'memory_limit':'512MB','threads':2})
    db.read_parquet(str(out/'facts_*.parquet')).create_view('facts')
    codes=[x[0] for x in db.execute("SELECT DISTINCT stock_code FROM facts WHERE diagnostic_classification='up' OR independent_event='up' ORDER BY 1").fetchall()]
    all_cycles=[];case_results=[]
    case_years={'002432.SZ':2021,'000957.SZ':2022,'603178.SH':2023,'603960.SH':2024,'603580.SH':2024}
    reader=db.execute('SELECT * FROM facts WHERE stock_code=ANY(?) ORDER BY stock_code,trade_date',[codes]).to_arrow_reader(batch_size=16384)
    records=(row for batch in reader for row in batch.to_pylist())
    for code,stock_rows in groupby(records,key=lambda x:x['stock_code']):
        facts={x['trade_date']:x for x in stock_rows}
        byday={d:dict(classification=f['diagnostic_classification'],in_target=True) for d,f in facts.items()}
        real_events={d:dict(consecutive_limit_days=None,observed_at=max(x['observed_at'] for x in events[code,d])) for d in facts if any(x['kind']=='up' for x in events.get((code,d),[]))}
        found=cycles(code,cal,byday,real_events,require_event=False)
        represented={p['trade_date'] for c in found for p in c['progress']}
        # Unknown source positives remain visible; missing state cannot erase them.
        for d,f in facts.items():
            if f['independent_event']=='up' and d not in represented:
                found.append(dict(stock_code=code,first_observed_date=d,first_board_date=None,left_boundary_verified=False,
                    observed_height=1,progress=[dict(trade_date=d,height_asof=None,provider_height=None)],cycle_complete=False,right_boundary_date=None,max_height=None,formal_eligible=False,quality='unverified_source_event',**{f'reached_{n}':None for n in (1,3,5,7,10)}))
        for c in found:
            start=c['first_observed_date'];end=c['right_boundary_date'] or c['progress'][-1]['trade_date'];prior=cal[cal.index(start)-1] if cal.index(start) else None
            span=[d for d in cal if (prior or start)<=d<=end]
            approved=c['cycle_complete'] and prior is not None and all(facts.get(d,{}).get('formal_eligible') is True for d in span)
            c.update(label_version=VERSION,cycle_id=f"{code}:{start}",formal_eligible=approved,
                acceptance={k:approved for k in ('sources_verified','rules_validated','coverage_complete','strict_facts_complete')},
                left_boundary_date=prior,first_board_year=(c['first_board_date'] or start).year,
                evidence_kind='independently_confirmed' if approved else 'price_rules_with_explicit_assumptions',
                unknown_reasons=sorted({facts[d]['reason'] for d in span if d in facts and facts[d]['status']=='unknown'}),
                diagnostic_boundary_reasons={'left':facts.get(prior,{}).get('diagnostic_reason','outside_observation_window'),'right':facts.get(end,{}).get('diagnostic_reason','outside_observation_window')},
                state_sources=sorted({eid for d in span for eid in facts.get(d,{}).get('state_evidence_ids',[])}),
                event_sources=sorted({eid for d in span for eid in facts.get(d,{}).get('event_evidence_ids',[])}))
            c['research_state']=research_state(c,publication_accepted=approved)
            c['max_height_state']=c['research_state'] if c['cycle_complete'] else 'unknown'
            all_cycles.append(c)
        if code in case_years:
            year=case_years[code];subset=[f for d,f in facts.items() if d.year==year]
            case_results.append(dict(stock_code=code,year=year,price_rows=sum(f['price_present'] for f in subset),sessions=len(subset),st_known=sum(f['is_st'] is not None for f in subset),
                status_counts=dict(Counter(f['status'] for f in subset)),diagnostic_counts=dict(Counter(f['diagnostic_classification'] for f in subset)),
                unknown_reasons=dict(Counter(f['reason'] for f in subset if f['status']=='unknown')),
                cycles=[c for c in found if c['first_board_year']==year]))
    # Cases without ANY candidate anywhere must also be reported.
    reported={c['stock_code'] for c in case_results}
    for code,year in case_years.items():
        if code not in reported:
            rows=db.execute('SELECT price_present,is_st,status,diagnostic_classification,reason FROM facts WHERE stock_code=? AND year(trade_date)=?',[code,year]).fetchall()
            case_results.append(dict(stock_code=code,year=year,price_rows=sum(x[0] for x in rows),sessions=len(rows),st_known=sum(x[1] is not None for x in rows),status_counts=dict(Counter(x[2] for x in rows)),diagnostic_counts=dict(Counter(x[3] for x in rows)),unknown_reasons=dict(Counter(x[4] for x in rows if x[2]=='unknown')),cycles=[]))
    for y in yearly:
        group=[c for c in all_cycles if c['first_board_year']==y['year']]
        y.update(research_state_counts={s:sum(c['research_state']==s for c in group) for s in ('verified','diagnostic','unknown')},
            complete_diagnostic_cycles=sum(c['cycle_complete'] and c['research_state']=='diagnostic' for c in group),
            diagnostic_first_boards=sum(c['first_board_date'] is not None and c['research_state']=='diagnostic' for c in group),
            diagnostic_heights={str(n):sum(c['cycle_complete'] and c['max_height']>=n and c['research_state']=='diagnostic' for c in group) for n in (3,5,7,10)},
            formal_first_boards=sum(c['research_state']=='verified' for c in group),
            formal_stock_day_coverage=(y.get('confirmed_limit_up',0)+y.get('confirmed_not_limit_up',0))/y['rows'] if y['rows'] else None,
            diagnostic_stock_day_coverage=sum(v for k,v in y['diagnostic_counts'].items() if k!='unknown')/y['rows'] if y['rows'] else None)
    traces=list(csv.DictReader((BASE/'dragon_diagnostics_v051/cycle_exclusions.csv').open()));blocked=[]
    for t in traces:
        key='left_boundary_date' if t['left_reason']=='historical_st_missing' else 'right_boundary_date' if t['left_reason']=='established' and t['right_reason']=='historical_st_missing' else None
        if key:
            d=date.fromisoformat(t[key]);st=resolve_state(states.get((t['stock_code'],d),[]),d)
            blocked.append(dict(cycle_id=t['cycle_id'],stock_code=t['stock_code'],boundary_date=d,st_observed=st['is_st'] is not None,quality=st['quality']))
    save(out/'blocked_693.json',blocked);save(out/'cycles.json',all_cycles);save(out/'cases.json',case_results)
    # Explicit missing official-price list is compact by trade date, never hidden as zeros.
    missing=db.execute('SELECT trade_date,count(*) stock_days FROM facts WHERE NOT official_limit_available GROUP BY 1 ORDER BY 1').fetchall()
    pq.write_table(pa.Table.from_pylist([dict(trade_date=d,missing_official_limit_stock_days=n) for d,n in missing]),out/'missing_limit_dates.parquet',compression='zstd')
    summary=dict(version=VERSION,built_at_utc=datetime.now(timezone.utc),yearly=yearly,
        audit_sessions=len(cal),baseline_audit_sessions=31,st_blocked_cycles=len(blocked),st_blocked_resolved=sum(x['st_observed'] for x in blocked),
        states={s:sum(c['research_state']==s for c in all_cycles) for s in ('verified','diagnostic','unknown')},
        candidate_cycles=len(all_cycles),complete_cycles=sum(c['cycle_complete'] for c in all_cycles),strict_accepted=any(c['formal_eligible'] for c in all_cycles),
        missing_official_limit_dates=len(missing),event_source_sessions=len({d for _,d,_ in pools}),event_issues=issues,
        state_observation_sha256=hashlib.sha256(statepath.read_bytes()).hexdigest(),
        formal_annual_eligible_years=[],warning='Audited date coverage is not verified label coverage; price-only diagnostics are not full-market annual statistics')
    save(out/'summary.json',summary)
    files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()}
    save(ROOT/'manifest.json',dict(dataset=str(out.relative_to(ROOT)),files=files,version=VERSION));print(summary,flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--resume');args=parser.parse_args();build(args.resume)
