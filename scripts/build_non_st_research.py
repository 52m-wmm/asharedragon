"""Local, versioned research eligibility overlay. No raw/market/DB writes or APIs."""
from pathlib import Path
from collections import Counter,defaultdict
from datetime import date
import csv,hashlib,json
import duckdb
import pyarrow.parquet as pq
from app.research.non_st_scope import VERSION,day_eligibility,aggregate
from app.research.stock_status import resolve_security

BASE=Path.home()/'Datasets/asharedragon/research';ROOT=BASE/'dragon_non_st_v1'
PS=BASE/'dragon_seasonality_exploratory/snapshots/c3355c56960ddd49fab8'
P52=BASE/'dragon_v052/snapshots/20261107T142031408652Z'
P56=BASE/'dragon_v056/snapshots/20261108T044422935371Z'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,o):p.write_text(json.dumps(o,ensure_ascii=False,default=str,indent=2))

def counts(rows):
    c=Counter(r['research_outcome'] for r in rows)
    return dict(total=len(rows),success=c['success'],failure=c['failure'],unknown=c['unknown'],excluded=c['excluded'])

def build():
    preserved={str(p):sha(p) for p in BASE.glob('*/manifest.json') if p.parent!=ROOT}
    samples=read(PS/'samples.json');direct={r['fact_id']:r for r in read(P56/'facts.json')};diag={r['cycle_id']:r for r in read(P52/'cycles.json')}
    calendar=[r['trade_date'] for r in pq.read_table(BASE/'calendar.parquet').to_pylist()];calendar=sorted(set(calendar));index={str(d):i for i,d in enumerate(calendar)}
    states=defaultdict(list);aliases=defaultdict(list)
    for r in pq.read_table(BASE/'dragon_v053/historical_stock_status.parquet').to_pylist():states[r['stock_code']].append(r)
    for r in pq.read_table(BASE/'dragon_v053/security_aliases.parquet').to_pylist():aliases[r['stock_code']].append(r)
    traces={r['cycle_id']:r for r in csv.DictReader((BASE/'dragon_diagnostics_v051/cycle_exclusions.csv').open())}
    blocked=read(P52/'blocked_693.json');assert len(blocked)==693
    keys=set();progress={}
    for r in samples:
        original=(direct if r['cohort']=='direct' else diag)[r['cycle_id']]
        days=[str(x['trade_date']) for x in original['progress']]
        assert days and days==sorted(set(days))
        progress[r['cohort'],r['cycle_id']]=days;keys.update((r['stock_code'],d) for d in days)
    blocked_days={}
    for r in blocked:
        t=traces[r['cycle_id']];i=index[t['first_observed_date']];days=[str(d) for d in calendar[i:i+int(t['observed_run_length'])]]
        blocked_days[r['cycle_id']]=days;keys.update((r['stock_code'],d) for d in days+[r['boundary_date']])
    resolved={}
    for code,ds in sorted(keys):
        d=date.fromisoformat(ds);sid=resolve_security(code,d,aliases[code])
        # No saved certified ordinary-regime observations currently exist. In
        # particular tradestatus=1, plain names and 20% returns are not such proof.
        resolved[code,ds]=dict(stock_code=code,trade_date=ds,**day_eligibility(code,d,states[code],identity=sid))
    rows=[]
    for r in samples:
        days=progress[r['cohort'],r['cycle_id']]
        # Binary success needs only its first five up dates; later ST must not
        # retroactively erase an already eligible five-board outcome.
        needed=days[:5] if r['outcome']=='success' else days
        q=aggregate([resolved[r['stock_code'],d] for d in needed]);full=aggregate([resolved[r['stock_code'],d] for d in days])
        research='excluded' if q['status']=='excluded' else r['outcome'] if q['status']=='eligible' else 'unknown'
        board=r['board'];ds=r['first_board_date'] or days[0]
        regime='main_10' if board=='main' else 'chinext_20' if board=='chinext' and ds>='2020-08-24' else 'chinext_10' if board=='chinext' else 'star_20' if board=='star' else 'other'
        rows.append(dict(cohort=r['cohort'],cycle_id=r['cycle_id'],stock_code=r['stock_code'],first_board_date=r['first_board_date'],
            first_observed_date=days[0],year=r['audit_year'],month=r['start_month'],regime=regime,board=board,
            historical_outcome=r['outcome'],research_outcome=research,eligibility=q,cycle_eligibility=full,
            required_dates=needed,observed_dates=days))
    # Selected derived facts only, never rescanning original OHLC or raw responses.
    with duckdb.connect(config={'memory_limit':'256MB','threads':2}) as db:
        import pyarrow as pa
        db.register('keys',pa.Table.from_pylist([dict(stock_code=r['stock_code'],trade_date=date.fromisoformat(r['boundary_date'])) for r in blocked]))
        result=db.execute('select f.* from read_parquet(?) f join keys using(stock_code,trade_date)',[str(P56/'stock_day_evidence.parquet')])
        names=[c[0] for c in result.description];facts={(r['stock_code'],str(r['trade_date'])):r for r in (dict(zip(names,row)) for row in result.fetchall())}
    audit=[]
    for r in blocked:
        f=facts.get((r['stock_code'],r['boundary_date']),{});status=f.get('status','unknown')
        q=aggregate([resolved[r['stock_code'],d] for d in blocked_days[r['cycle_id']]])
        audit.append(dict(**r,old_block='historical_st_missing',boundary_limit_status=status,
            limit_status_st_gate_redundant=status!='unknown',boundary_evidence_ids=f.get('evidence_ids',[]),
            remaining_limit_need=None if status!='unknown' else 'independent_close_status_or_verified_limit_not_necessarily_ST',
            eligibility=q,research_action='exclude_no_collection' if q['status']=='excluded' else 'already_eligible_no_ST_collection' if q['status']=='eligible' else 'need_qualification_evidence_only_for_selected_research_window'))
    summary=dict(version=VERSION,cohorts={c:counts([r for r in rows if r['cohort']==c]) for c in ('direct','reconstruction')},
        groups=[dict(cohort=c,year=y,regime=g,**counts([r for r in rows if r['cohort']==c and r['year']==y and r['regime']==g])) for c in ('direct','reconstruction') for y in range(2017,2027) for g in ('main_10','chinext_10','chinext_20','star_20')],
        audit_693=dict(total=693,limit_status_ST_gate_redundant=sum(r['limit_status_st_gate_redundant'] for r in audit),
            limit_status_still_unknown=sum(not r['limit_status_st_gate_redundant'] for r in audit),
            research_eligibility=dict(Counter(r['eligibility']['status'] for r in audit))),
        stock_days=dict(Counter(r['status'] for r in resolved.values())),non_st_observed_days=sum(r['non_st_confirmed'] for r in resolved.values()),
        exclusion_reasons=dict(Counter(x for r in resolved.values() if r['status']=='excluded' for x in r['reasons'])),
        unknown_reasons=dict(Counter(x for r in resolved.values() if r['status']=='unknown' for x in r['reasons'])),
        normal_regime_proofs=0,formal_years=[],
        policy='Known historical ST/*ST or delisting dates excluded; no current-name or turnover inference; unknown not failure',
        ordinary_trading_not_delisting='BaoStock tradestatus=1 alone only distinguishes suspension, not the delisting arrangement',
        source_contract_warning='Tushare doc 298 excludes ST in description, but archived XR*ST/XD*ST rows contradict blanket use; contract not accepted as non-ST truth')
    summary['non_st_only_windows']={c:sum(all(resolved[r['stock_code'],d]['non_st_confirmed'] for d in r['required_dates']) for r in rows if r['cohort']==c) for c in ('direct','reconstruction')}
    summary['excluded_previous_outcomes']={c:dict(Counter(r['historical_outcome'] for r in rows if r['cohort']==c and r['research_outcome']=='excluded')) for c in ('direct','reconstruction')}
    # Existing strict reconstruction publication contains no certified ordinary
    # regime records. New certificates must be reviewed, not silently ignored.
    strict_path=BASE/'historical_limits_v1/formal_l1_labels.json'
    assert not read(strict_path), 'New strict evidence needs a targeted qualification review'
    inputs=[PS/'samples.json',P56/'facts.json',P56/'stock_day_evidence.parquet',P52/'cycles.json',P52/'blocked_693.json',BASE/'dragon_diagnostics_v051/cycle_exclusions.csv',strict_path,BASE/'dragon_v053/historical_stock_status.parquet',BASE/'dragon_v053/security_aliases.parquet',BASE/'calendar.parquet',Path(__file__),Path('backend/app/research/non_st_scope.py')]
    hashes={str(p):sha(p) for p in inputs};stamp=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()[:20]
    out=ROOT/'snapshots'/stamp
    if out.exists():
        m=read(ROOT/'manifest.json');assert m['dataset']==str(out.relative_to(ROOT)) and all(sha(out/n)==h for n,h in m['files'].items())
        print(json.dumps(dict(reused=True,**summary),ensure_ascii=False));return
    out.mkdir(parents=True)
    for name,obj in [('samples',rows),('days',list(resolved.values())),('audit_693',audit),('summary',summary),('provenance',dict(input_sha256=hashes,preserved_manifests=preserved))]:save(out/(name+'.json'),obj)
    assert all(sha(Path(p))==h for p,h in preserved.items())
    save(ROOT/'manifest.json',dict(version=VERSION,dataset=str(out.relative_to(ROOT)),files={p.name:sha(p) for p in out.iterdir()}))
    print(json.dumps(dict(snapshot=stamp,**summary),ensure_ascii=False))

if __name__=='__main__':build()
