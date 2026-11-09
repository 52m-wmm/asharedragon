"""Local-only v0.7 evidence inventory, binary outcomes and T+1 feature layers."""
from collections import Counter,defaultdict
from datetime import date,datetime,time,timezone
from pathlib import Path
import hashlib,json
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from app.db.session import engine
from app.providers.normalize import stock_code
from app.research.queries import Research
from app.research.stock_status import resolve_security
from app.research.feature_study import features_before_close,board_context,finite
from app.research.study_v07 import VERSION,FEATURES,cycle_outcome,decision_cutoff,availability,study_statistics,TZ
from build_us_market_signals import event_snapshot

BASE=Path.home()/'Datasets/asharedragon/research';ROOT=BASE/'dragon_v07'
PARENT=BASE/'dragon_v056/snapshots/20261108T044422935371Z'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,obj):p.write_text(json.dumps(obj,ensure_ascii=False,default=str,allow_nan=False,indent=2))
def build():
    original=json.loads((PARENT/'samples.json').read_text());parentfacts={x['fact_id']:x for x in json.loads((PARENT/'facts.json').read_text())}
    inputs={str(p):digest(p) for p in PARENT.iterdir() if p.is_file()}
    for p in [Path(__file__),Path('backend/app/research/study_v07.py'),Path('backend/app/research/feature_study.py'),Path('backend/app/research/dragons.py'),Path('backend/app/research/queries.py'),Path('backend/app/research/stock_status.py'),Path('backend/app/providers/normalize.py'),Path('backend/app/collectors/tushare_history.py'),Path('scripts/build_us_market_signals.py'),BASE/'calendar.parquet',BASE/'dragon_v053/security_aliases.parquet']:
        inputs[str(p)]=digest(p)
    cal,pools,evidence,issues=event_snapshot()
    inventory=defaultdict(Counter);raws=[]
    with engine.connect() as conn,conn.begin():
        conn.execute(text('SET TRANSACTION READ ONLY'))
        raws=[dict(r) for r in conn.execute(text("select id,provider,endpoint,raw_payload,fetched_at,sha256 from raw_observations where ingestion_status='success' and endpoint in ('limit_list_d','query_history_k_data_plus','stk_limit','stock_st','daily_basic','namechange','suspend_d')")).mappings()]
        auxiliary_counts=[dict(r) for r in conn.execute(text('select endpoint,count(*) rows from proxy_facts group by 1')).mappings()]
    raw_hashes={str(r['id']):r['sha256'] for r in raws}
    # Inspect contents, not directory years; archive duplication is counted separately.
    archive_years=Counter();archive_files=0
    for p in Path('data/raw/tushare').rglob('*.json'):
        obj=json.loads(p.read_text());records=obj.get('payload',{}).get('records',[]) if isinstance(obj,dict) else []
        for r in records:
            ds=str(r.get('trade_date',''))
            if len(ds)>=4:archive_years[ds[:4]]+=1
        archive_files+=1
        inputs[str(p)]=digest(p)
    baorows=defaultdict(list)
    for raw in raws:
        for r in raw['raw_payload'].get('records',[]):
            ds=str(r.get('trade_date') or r.get('date') or '')
            inventory[raw['endpoint']][ds[:4]]+=1
            if raw['endpoint']=='query_history_k_data_plus' and r.get('adjustflag')=='3':
                baorows[stock_code(r['code']),date.fromisoformat(r['date'])].append((r,raw))
    candidates=defaultdict(list)
    for (provider,d,kind),pool in pools.items():
        if kind=='U':
            for r in pool['rows']:candidates[r['stock_code'],d].append((r,pool,provider))
    status_path=BASE/'dragon_v053/historical_stock_status.parquet'
    inputs[str(status_path)]=digest(status_path)
    status_counts=Counter((r['valid_from'].year,r['risk_warning_status'],r['trading_status']) for r in pq.read_table(status_path).to_pylist())
    aliases=defaultdict(list)
    for a in pq.read_table(BASE/'dragon_v053/security_aliases.parquet').to_pylist():aliases[a['stock_code']].append(a)
    samples=[]
    with Research() as research:
        calendar=[r['trade_date'] for r in research.query('select distinct trade_date from calendar order by 1')]
        keys=set()
        for s in original:
            if not s['first_board_date']:continue
            d=date.fromisoformat(s['first_board_date']);keys.update((s['stock_code'],x) for x in [x for x in calendar if x<d][-40:]+[d])
        research.db.register('keys',pa.Table.from_pylist([dict(stock_code=c,trade_date=d) for c,d in sorted(keys)]))
        bars=defaultdict(dict)
        for r in research.query('select d.* from daily d join keys using(stock_code,trade_date)'):
            if r['trade_date'] in bars[r['stock_code']]:raise ValueError('Duplicate stock date')
            bars[r['stock_code']][r['trade_date']]=r
        for relative in research.catalog['datasets']:
            dataset=research.root/relative;m=json.loads((dataset/'manifest.json').read_text())
            for entry in m['months'].values():p=dataset/entry['file'];inputs[str(p)]=digest(p)
        for s in original:
            code=s['stock_code'];start=date.fromisoformat(s['first_board_date']) if s['first_board_date'] else None
            observed=date.fromisoformat(s['first_observed_date']);d=start or observed
            sid=resolve_security(code,d,aliases[code]);category,reason=cycle_outcome(s)
            if sid is None:category,reason='unknown','security_identity_unresolved'
            board,regime=board_context(code,d);cut=decision_cutoff(start,calendar) if start else None
            values={k:None for k in FEATURES};details={};source_meta={};bar=bars[code].get(start)
            if start:
                lag,_,lagprov=features_before_close(start,calendar,bars[code]);values.update(lag)
                for k,prov in lagprov.items():source_meta[k]=dict(source=prov['sources'],evidence_id=f'local_parquet:{code}:{start}',event_at=prov['market_event_end_utc'],fetched_at=None,window_dates=prov['window_dates'])
                valid=bar and bar.get('adjustment_type')=='none' and all(finite(bar.get(k)) and bar[k]>0 for k in ('open','high','low','close','volume','amount')) and bar['low']<=min(bar['open'],bar['close'])<=max(bar['open'],bar['close'])<=bar['high']
                if valid:
                    for k in ('open','high','low','close','volume','amount'):
                        values[k]=bar[k];source_meta[k]=dict(source=bar['source'],evidence_id=f'local_parquet:{code}:{start}',fetched_at=None)
                # Conflicts remain NULL. Never use today's valuation or names.
                observations=defaultdict(list)
                for row,pool,provider in candidates.get((code,start),[]):
                    for k in ('circulating_market_cap','total_market_cap','turnover_rate','open_times','seal_amount'):
                        if finite(row.get(k)):observations[k].append((float(row[k]),pool,provider))
                    t=row.get('first_limit_time')
                    if t and time(9,15)<=t<=time(15):observations['first_seal_seconds'].append((t.hour*3600+t.minute*60+t.second,pool,provider))
                for k,obs in observations.items():
                    if len({o[0] for o in obs})==1:
                        values[k]=obs[0][0];_,pool,provider=min(obs,key=lambda o:o[1]['observed_at'])
                        source_meta[k]=dict(source=provider,evidence_id=f"raw:{pool['raw_id']}:{pool['hash']}",fetched_at=pool['observed_at'])
                for k,rawkey in [('pe','peTTM'),('pb','pbMRQ'),('turnover_rate','turn')]:
                    obs=[]
                    for r,raw in baorows.get((code,start),[]):
                        if valid and finite(r.get('close')) and abs(float(r['close'])-bar['close'])<=.011 and finite(r.get(rawkey)):obs.append((float(r[rawkey]),raw))
                    if obs and len({v for v,_ in obs})==1:
                        if finite(values[k]) and abs(values[k]-obs[0][0])>.02:values[k]=None;source_meta[k]=dict(source='conflicting_sources',conflict=True)
                        else:
                            values[k]=obs[0][0];raw=obs[0][1];source_meta[k]=dict(source='baostock:'+rawkey,evidence_id=f"raw:{raw['id']}:{raw['sha256']}",fetched_at=raw['fetched_at'])
                ps=[(provider,pool) for (provider,dt,kind),pool in pools.items() if dt==start and kind=='U']
                if len(ps)==1:
                    provider,pool=ps[0];values['provider_limit_up_count']=len(pool['rows'])
                    source_meta['provider_limit_up_count']=dict(source=provider,evidence_id=f"raw:{pool['raw_id']}:{pool['hash']}",fetched_at=pool['observed_at'],scope='provider event universe, not full market including ST')
            for k,v in values.items():
                src=source_meta.get(k,{})
                event=src.get('event_at') or (datetime.combine(start,time(15),TZ).astimezone(timezone.utc) if start else None)
                if k=='first_seal_seconds' and finite(v):event=datetime.combine(start,time(int(v)//3600,int(v)%3600//60,int(v)%60),TZ).astimezone(timezone.utc)
                details[k]={**availability(v,event_at=event,cutoff=cut,fetched_at=src.get('fetched_at'),evidence_id=src.get('evidence_id')),'source':src.get('source'),'window_dates':src.get('window_dates'),'source_conflict':src.get('conflict',False)}
            pf=parentfacts[s['cycle_id']]
            samples.append(dict(cycle_id=s['cycle_id'],stock_code=code,security_id=sid,first_board_date=s['first_board_date'],first_observed_date=s['first_observed_date'],
                year=d.year,month=d.month,year_basis='first_board_date' if start else 'first_observed_only',board=board,regime=regime,actual_limit_rate=None,
                category=category,outcome=1 if category=='success' else 0 if category=='failure' else None,outcome_reason=reason,
                label_version=VERSION,parent_label_version=s['label_version'],label_evidence_ids=pf['evidence_ids'],
                observed_min_height=s['observed_min_height'],exact_max_streak=s['exact_max_streak'],complete_cycle=s['complete_cycle_eligible'],
                legacy_primary_included=s['local_research_eligible'],legacy_missing_observation_dates=s['missing_observation_dates'],blockers=s['blockers'],
                decision_cutoff_utc=cut.isoformat() if cut else None,strict_candidate_eligible=False,candidate_availability_reason='historical_first_board_assertion_publication_unverified',
                exploratory_features={k:v['exploratory_value'] for k,v in details.items()},strict_features={k:v['strict_value'] for k,v in details.items()},feature_evidence=details))
    assert len({r['cycle_id'] for r in samples})==len(samples)
    knownstarts=[(r['security_id'],r['first_board_date']) for r in samples if r['first_board_date']]
    assert len(knownstarts)==len(set(knownstarts))
    excluded=[r for r in samples if r['complete_cycle'] and not r['legacy_primary_included']]
    assert len(excluded)==240 and all(r['category']=='failure' for r in excluded)
    years=json.loads((PARENT/'years.json').read_text())
    for y in years:
        ss=[r for r in samples if r['year']==y['year']];counts=Counter(r['category'] for r in ss);resolved=counts['success']+counts['failure']
        y.update(success=counts['success'],failure=counts['failure'],unknown_candidates=counts['unknown'],local_candidates=len(ss),
            unknown_candidate_fraction=counts['unknown']/len(ss) if ss else None,local_resolved_fraction=resolved/len(ss) if ss else None,
            restored_success=sum(r['category']=='success' and not r['legacy_primary_included'] for r in ss),restored_failure=sum(r['category']=='failure' and not r['legacy_primary_included'] for r in ss),
            primary_blocker='direct_positive_evidence_absent' if not y['confirmed_up'] else 'boundary_evidence_and_population_coverage',
            feature_missing={k:1-sum(finite(r['exploratory_features'][k]) for r in ss if r['category']!='unknown')/resolved if resolved else None for k in FEATURES},
            full_market_sample_coverage=None,formal_year_eligible=False)
    libraries={k:__import__(k).__version__ for k in ('numpy','scipy','duckdb','pyarrow')}
    input_key=dict(files=inputs,raw_hashes=raw_hashes,pool_evidence=evidence,libraries=libraries)
    snapshot=hashlib.sha256(json.dumps(input_key,sort_keys=True,default=str).encode()).hexdigest()[:20];out=ROOT/'snapshots'/snapshot
    if out.exists():
        manifest=json.loads((ROOT/'manifest.json').read_text())
        if manifest['dataset']!=str(out.relative_to(ROOT)) or any(digest(out/n)!=sha for n,sha in manifest['files'].items()):raise ValueError('Snapshot conflict')
        print(json.dumps(dict(snapshot=snapshot,reused=True)));return
    meta=dict(version=VERSION,snapshot=snapshot,libraries=libraries,parent_snapshot=str(PARENT),input_sha256=inputs,raw_hashes=raw_hashes,
        raw_endpoint_year_counts={k:dict(v) for k,v in inventory.items()},archive_files=archive_files,archive_record_year_counts=dict(archive_years),auxiliary_counts=auxiliary_counts,
        historical_status_inventory=[dict(year=y,risk_warning_status=r,trading_status=t,observations=n) for (y,r,t),n in sorted(status_counts.items())],
        pool_issues=issues,source_sessions={str(y):len({d for _,d,_ in pools if d.year==y}) for y in range(2017,2027)},
        categories=dict(Counter(r['category'] for r in samples)),restored_240=len(excluded),formal_years=[],
        temporal_policy='T close to next SSE session 09:25 exclusive; facts, actual capture and verified first-publication times separate',
        source_scope='Local-only observations; no new remote market data; absence of an event never implies non-limit-up',
        minimal_repairs=[dict(year=y,priority=1,need='Independent closing-limit events or official limit prices for a consecutive interval and its boundaries, for all candidates not only famous winners',existing_positive_events=next(r['confirmed_up'] for r in years if r['year']==y),restored_success=next(r['restored_success'] for r in years if r['year']==y),restored_failure=next(r['restored_failure'] for r in years if r['year']==y)) for y in range(2021,2026)])
    stats={mode:study_statistics(samples,mode) for mode in ('exploratory','strict')}
    out.mkdir(parents=True)
    for name,data in [('metadata',meta),('samples',samples),('years',years),('statistics',stats),('restored_240',excluded),('pool_evidence',evidence)]:save(out/(name+'.json'),data)
    pq.write_table(pa.Table.from_pylist(samples),out/'samples.parquet',compression='zstd')
    for category in ('success','failure','unknown'):pq.write_table(pa.Table.from_pylist([r for r in samples if r['category']==category]),out/(category+'.parquet'),compression='zstd')
    # Feature layers physically separate, preventing accidental exploratory fallback.
    for mode in ('strict','exploratory'):
        pq.write_table(pa.Table.from_pylist([dict(cycle_id=r['cycle_id'],stock_code=r['stock_code'],first_board_date=r['first_board_date'],decision_cutoff_utc=r['decision_cutoff_utc'],**r[mode+'_features']) for r in samples]),out/(mode+'_features.parquet'),compression='zstd')
    manifest=dict(dataset=str(out.relative_to(ROOT)),version=VERSION,files={p.name:digest(p) for p in out.iterdir()})
    save(ROOT/'manifest.tmp',manifest);(ROOT/'manifest.tmp').replace(ROOT/'manifest.json')
    print(json.dumps(dict(snapshot=snapshot,categories=meta['categories'],restored240=len(excluded),cohort=stats['exploratory']['cohort']),ensure_ascii=False))
if __name__=='__main__':build()
