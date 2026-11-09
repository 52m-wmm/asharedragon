"""Reuse immutable summaries; inspect only new archive names / raw metadata, no old raw payload scans."""
from pathlib import Path
from collections import Counter
import hashlib,json
from datetime import date,datetime,timezone
from sqlalchemy import text
from app.db.session import engine
from app.research.feature_study import board_context
from app.research.seasonality_exploratory import VERSION,statistics

BASE=Path.home()/'Datasets/asharedragon/research';ROOT=BASE/'dragon_seasonality_exploratory'
P07=BASE/'dragon_v07/snapshots/49bfba76ba9a1247695e'
P52=BASE/'dragon_v052/snapshots/20261107T142031408652Z'
def save(p,obj):p.write_text(json.dumps(obj,ensure_ascii=False,default=str,allow_nan=False,indent=2))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def build():
    m=json.loads((P07/'metadata.json').read_text());pool=json.loads((P07/'pool_evidence.json').read_text())
    known={int(k):v for k,v in m['raw_hashes'].items()};known.update({x['raw_id']:x['hash'] for x in pool})
    with engine.connect() as c,c.begin():
        c.execute(text('SET TRANSACTION READ ONLY'))
        raw=[dict(r) for r in c.execute(text("select id,endpoint,sha256 from raw_observations where ingestion_status='success' and endpoint in ('limit_list_d','query_history_k_data_plus','stk_limit','stock_st','daily_basic','namechange','suspend_d','stock_zt_pool_em','stock_zt_pool_zbgc_em')")).mappings()]
    changed=[r for r in raw if r['id'] not in known or known[r['id']]!=r['sha256']]
    oldfiles={k for k in m['input_sha256'] if k.startswith('data/raw/tushare/')}
    currentfiles={str(p) for p in Path('data/raw/tushare').rglob('*.json')};newfiles=sorted(currentfiles-oldfiles)
    if changed or newfiles:raise ValueError(f'New independent evidence requires targeted review: {len(changed)} raw records, {len(newfiles)} files; do not publish stale no-new-evidence finding')
    inventory=dict(checked_at_utc=datetime.now(timezone.utc),method='PG IDs+hash metadata and file-name inventory only; old raw files not opened',new_raw_records=0,new_archive_files=0,raw_metadata_rows=len(raw),archive_file_count=len(currentfiles),reused_v07_inventory=True)
    direct=json.loads((P07/'samples.json').read_text());diag=json.loads((P52/'cycles.json').read_text());oldsummary=json.loads((P52/'summary.json').read_text())
    rows=[]
    for r in direct:
        start=r['first_board_date'];obs=r['first_observed_date']
        rows.append(dict(cohort='direct',cycle_id=r['cycle_id'],stock_code=r['stock_code'],security_id=r['security_id'],first_board_date=start,
            audit_year=int((start or obs)[:4]),start_month=int(start[5:7]) if start else None,observation_month=int(obs[5:7]),
            board=r['board'],regime=r['regime'],evidence_grade='verified' if r['category']!='unknown' else 'unknown',outcome=r['category'],
            features=r['exploratory_features'],observed_height=r['observed_min_height'],exact_height=r['exact_max_streak'],reasons=[r['outcome_reason']],label_version=r['label_version']))
    # Match security identity to dated alias catalogue, never names.
    import pyarrow.parquet as pq
    from app.research.stock_status import resolve_security
    aliases=pq.read_table(BASE/'dragon_v053/security_aliases.parquet').to_pylist();bycode={}
    for a in aliases:bycode.setdefault(a['stock_code'],[]).append(a)
    for r in diag:
        start=r['first_board_date'];obs=r['first_observed_date'];day=date.fromisoformat(start or obs);board,regime=board_context(r['stock_code'],day)
        grade=r['research_state'];height=r['observed_height'];exact=r['max_height']
        outcome='unknown'
        if grade=='diagnostic' and start:
            if height>=5:outcome='success'
            elif r['cycle_complete'] and exact is not None and exact<5:outcome='failure'
        rows.append(dict(cohort='reconstruction',cycle_id=r['cycle_id'],stock_code=r['stock_code'],security_id=resolve_security(r['stock_code'],day,bycode.get(r['stock_code'],[])),
            first_board_date=start,audit_year=day.year,start_month=day.month if start else None,observation_month=int(obs[5:7]),board=board,regime=regime,
            evidence_grade=grade,outcome=outcome,observed_height=height,exact_height=exact,reasons=r['unknown_reasons'],label_version=r['label_version']))
    if any(r['security_id'] is None and r['outcome']!='unknown' for r in rows):raise ValueError('Resolved outcome lacks stable identity')
    annual=json.loads((P07/'years.json').read_text())
    for y in annual:
        old=next(r for r in oldsummary['yearly'] if r['year']==y['year'])
        y.update(daily_row_coverage=old['prices']/old['rows'],daily_coverage_denominator=old['rows'],
            reconstructed_up_candidates=old['diagnostic_counts'].get('up',0),reconstruction_reasons=old['unknown_reasons'],
            diagnostic_stock_day_fraction=old['diagnostic_stock_day_coverage'],diagnostic_first_boards=old['diagnostic_first_boards'],
            complete_diagnostic_cycles=old['complete_diagnostic_cycles'])
    # Report overlap, but never union or silently deduplicate differently defined cycles.
    a={(r['stock_code'],r['first_board_date']) for r in rows if r['cohort']=='direct' and r['first_board_date']}
    b={(r['stock_code'],r['first_board_date']) for r in rows if r['cohort']=='reconstruction' and r['first_board_date']}
    files=[P07/'samples.json',P07/'years.json',P07/'metadata.json',P52/'cycles.json',P52/'summary.json',Path(__file__),Path('backend/app/research/seasonality_exploratory.py'),Path('backend/app/research/feature_study.py'),BASE/'dragon_v053/security_aliases.parquet']
    inputs={str(p):sha(p) for p in files};snap=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()[:20];out=ROOT/'snapshots'/snap
    if out.exists():
        manifest=json.loads((ROOT/'manifest.json').read_text())
        if manifest['dataset']!=str(out.relative_to(ROOT)) or any(sha(out/n)!=v for n,v in manifest['files'].items()):raise ValueError('Snapshot integrity mismatch')
        print(json.dumps(dict(snapshot=snap,reused=True)));return
    out.mkdir(parents=True)
    stats={cohort:statistics(rows,cohort) for cohort in ('direct','reconstruction')}
    meta=dict(version=VERSION,snapshot=snap,input_sha256=inputs,inventory=inventory,overlapping_code_start_pairs=len(a&b),formal_years=[],
        cohorts={'direct':'v0.7 independent closing facts; verified means local binary outcome, not representative annual coverage','reconstruction':'v0.5.2 price-rule scenario; diagnostic assumptions and old missing-state constraints retained, not exchange confirmation'},
        strict_backtest_eligible=False,raw_assumptions_source=str(P52),feature_study_snapshot=str(P07))
    for name,obj in [('samples',rows),('years',annual),('metadata',meta),('statistics',stats)]:save(out/(name+'.json'),obj)
    save(ROOT/'manifest.json',dict(dataset=str(out.relative_to(ROOT)),version=VERSION,files={p.name:sha(p) for p in out.iterdir()}))
    print(json.dumps(dict(snapshot=snap,overlap=len(a&b),summaries={k:v['summary'] for k,v in stats.items()}),ensure_ascii=False))
if __name__=='__main__':build()
