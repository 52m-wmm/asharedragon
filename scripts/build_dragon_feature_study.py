"""Freeze local v0.6 study; no network collectors or database writes."""
from collections import Counter,defaultdict
from datetime import date
from pathlib import Path
import hashlib,json,sys
import pyarrow as pa
import pyarrow.parquet as pq
from app.research.queries import Research
from app.research.stock_status import resolve_security
from app.research.feature_study import VERSION,analyze,features_before_close,board_context,cutoff

BASE=Path.home()/'Datasets/asharedragon/research'
PARENT=BASE/'dragon_v056/snapshots/20261108T044422935371Z'
ROOT=BASE/'dragon_feature_v06'
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,obj):path.write_text(json.dumps(obj,ensure_ascii=False,default=str,allow_nan=False,indent=2))
def build():
    samples=json.loads((PARENT/'samples.json').read_text());complete=[s for s in samples if s['complete_cycle_eligible']]
    assert len(complete)==5369 and sum(s['local_research_eligible'] for s in complete)==5129
    assert len({s['cycle_id'] for s in complete})==len(complete)
    inputs={str(p):digest(p) for p in [PARENT/'samples.json',PARENT/'years.json',BASE/'calendar.parquet',BASE/'dragon_v053/security_aliases.parquet',Path(__file__),Path('backend/app/research/feature_study.py')]}
    inputs.update({str(p):digest(p) for p in PARENT.iterdir() if p.is_file()})
    aliases=defaultdict(list)
    for a in pq.read_table(BASE/'dragon_v053/security_aliases.parquet').to_pylist():aliases[a['stock_code']].append(a)
    rows=[]
    with Research() as research:
        calendar=[x['trade_date'] for x in research.query('select distinct trade_date from calendar order by 1')]
        targets=[]
        for s in complete:
            d=date.fromisoformat(s['first_board_date']);prior=[v for v in calendar if v<d][-40:]
            targets.extend((s['stock_code'],v) for v in prior)
        research.db.register('targets',pa.Table.from_pylist([dict(stock_code=c,trade_date=d) for c,d in sorted(set(targets))]))
        bars=defaultdict(dict)
        for r in research.query('select d.* from daily d join targets using(stock_code,trade_date)'):
            if r['trade_date'] in bars[r['stock_code']]:raise ValueError('Duplicate quote')
            bars[r['stock_code']][r['trade_date']]=r
        # Snapshot hashes cover every source partition, never rewrite original data.
        for relative in research.catalog['datasets']:
            dataset=research.root/relative;m=json.loads((dataset/'manifest.json').read_text())
            for entry in m['months'].values():
                p=dataset/entry['file'];inputs[str(p)]=digest(p)
        for s in complete:
            d=date.fromisoformat(s['first_board_date']);sid=resolve_security(s['stock_code'],d,aliases[s['stock_code']])
            if not sid:raise ValueError('Ambiguous security identity')
            features,reasons,provenance=features_before_close(d,calendar,bars[s['stock_code']]);board,regime=board_context(s['stock_code'],d)
            rows.append(dict(cycle_id=s['cycle_id'],stock_code=s['stock_code'],security_id=sid,first_board_date=str(d),year=d.year,month=d.month,
                board=board,regime=regime,actual_limit_rate=None,primary_included=s['local_research_eligible'],outcome=int(s['exact_max_streak']>=5),
                outcome_evidence=dict(exact_max_streak=s['exact_max_streak'],parent_version=s['label_version'],complete_cycle=True),
                decision_cutoff_utc=cutoff(d).isoformat(),cutoff_exclusive=True,features=features,null_reasons=reasons,feature_provenance=provenance,
                archived={k:s.get(k) for k in ('circulating_market_cap','turnover_rate')},archive_provenance=s['feature_provenance'],
                missing_observation_dates=s['missing_observation_dates']))
    snapshot=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()[:20];out=ROOT/'snapshots'/snapshot
    if out.exists():
        manifest=json.loads((ROOT/'manifest.json').read_text())
        if manifest['dataset']!=str(out.relative_to(ROOT)) or any(digest(out/name)!=sha for name,sha in manifest['files'].items()):
            raise ValueError('Existing snapshot integrity or active manifest mismatch')
        print(json.dumps(dict(snapshot=snapshot,reused=True)));return
    stats=analyze(rows)
    excluded=[r for r in rows if not r['primary_included']]
    metadata=dict(version=VERSION,snapshot=snapshot,parent_snapshot=str(PARENT),input_sha256=inputs,
        complete_cycles=len(rows),primary_samples=stats['cohort']['n'],excluded=len(excluded),
        excluded_heights=dict(Counter(r['outcome_evidence']['exact_max_streak'] for r in excluded)),
        excluded_missing_days=dict(Counter(len(r['missing_observation_dates']) for r in excluded)),
        exclusion_reason='additional five-session classification window incomplete; closed-cycle outcome itself known',
        formal_years=[],parent_year_coverage=json.loads((PARENT/'years.json').read_text()),
        source='audited unadjusted local Parquet; parent independently verified local cycles; no new remote requests',
        methodology='Primary cohort frozen from v056. Closed-cycle sensitivity adds 240 proven sub-5 cycles. Cluster bootstrap separately by security and first-board date; not two-way joint inference. BH across six planned lagged features, one earliest cycle per security MWU sensitivity only.',
        libraries={k:__import__(k).__version__ for k in ('numpy','scipy','duckdb','pyarrow')})
    out.mkdir(parents=True);save(out/'samples.json',rows);save(out/'statistics.json',stats);save(out/'metadata.json',metadata)
    pq.write_table(pa.Table.from_pylist(rows),out/'samples.parquet',compression='zstd')
    save(out/'excluded.json',excluded)
    manifest=dict(dataset=str(out.relative_to(ROOT)),version=VERSION,files={p.name:digest(p) for p in out.iterdir()})
    ROOT.mkdir(exist_ok=True);save(ROOT/'manifest.tmp',manifest);(ROOT/'manifest.tmp').replace(ROOT/'manifest.json')
    print(json.dumps(dict(snapshot=snapshot,cohort=stats['cohort'],closed=stats['closed_cycle_sensitivity'],one_cycle=stats['one_cycle_sensitivity']),ensure_ascii=False))
if __name__=='__main__':build()
