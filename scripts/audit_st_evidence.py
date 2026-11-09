"""v0.5.3 offline evidence recovery and isolated ST counterfactual audit.
No requests, SQL mutations, price writes, or publication to production labels.
"""
from collections import Counter,defaultdict
from datetime import date,datetime,timezone
from pathlib import Path
import csv,hashlib,json,re
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from app.db.session import engine
from app.core.config import settings
from app.providers.normalize import stock_code
from app.research.queries import Research
from app.research.stock_status import security_id,resolve_security,status_at,risk_from_name
from app.research.limit_rules import reconstruct
from app.research.dragons import cycles

BASE=Path.home()/'Datasets/asharedragon/research'
ROOT=BASE/'dragon_v053'


def save(name,value):
    p=ROOT/name;p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,default=str,indent=2));tmp.replace(p)


def audit():
    ROOT.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((BASE/'dragon_v052/manifest.json').read_text());old=BASE/'dragon_v052'/manifest['dataset']
    inventory={'previous_snapshot':str(old),'network_requests':0,'credential_present':bool(settings.tushare_token),
               'token_validity':'unknown_not_expired_proven','credential_scope':'third_party_proxy_only_no_official_credential',
               'network_decision':'no_paid_proxy_calls; no credential forwarding; previous BaoStock failures circuit retained'}
    with engine.connect() as c,c.begin():
        c.execute(text('SET TRANSACTION READ ONLY'))
        inventory['db_endpoints']=[dict(x) for x in c.execute(text('select endpoint,ingestion_status,count(*) rows from raw_observations group by 1,2 order by 1')).mappings()]
        inventory['db_name_columns']=[dict(x) for x in c.execute(text("select table_name,column_name from information_schema.columns where table_schema='public' and column_name ilike '%name%'")).mappings()]
        stockrows=[dict(x) for x in c.execute(text("select stock_code,listing_date,delisting_date,raw_id from stocks where provider='baostock'")).mappings()]
        eventrows=[dict(x) for x in c.execute(text('select stock_code,stock_name,trade_date,provider,raw_id,fetched_at from limitup_events')).mappings()]
        relevant=[dict(x) for x in c.execute(text("select id,endpoint,raw_payload from raw_observations where endpoint in ('stock_st','stk_limit','namechange','st')")).mappings()]
    inventory['db_status_raw_rows']=len(relevant)
    if relevant:raise ValueError('New status payloads require explicit normalization; do not silently ignore')
    # Inventory preserved archives by actual envelope, not filename or DB membership.
    archive_counts=Counter();unreadable=[]
    for folder in (Path('data/tushare_proxy'),Path('data/raw/tushare'),Path('data/tushare_probe_archive')):
        if not folder.exists():continue
        for path in folder.rglob('*.json'):
            try:
                payload=json.loads(path.read_text());api=payload.get('endpoint') or payload.get('api_name') or payload.get('api')
                if api:archive_counts[api]+=1
            except (ValueError,OSError):unreadable.append(str(path))
    inventory['archive_endpoints']=dict(archive_counts);inventory['unreadable_archives']=unreadable
    retained=[]
    report=Path('docs/tushare_probe.md')
    if report.exists():
        for block in re.findall(r'```json\s*(.*?)```',report.read_text(),re.S):
            try:p=json.loads(block)
            except ValueError:continue
            if p.get('api') not in ('stock_st','stk_limit','namechange','st'):continue
            joinable=0
            for row in p.get('samples',[]):
                try:stock_code(row['ts_code']);joinable+=1
                except (ValueError,KeyError):pass
            retained.append(dict(endpoint=p['api'],reported_rows=p['rows'],retained_samples=len(p['samples']),joinable_samples=joinable,
                reason='redacted_or_missing_security_code',evidence_sha256=hashlib.sha256(report.read_bytes()).hexdigest()))
    inventory['legacy_report_samples']=retained
    save('inventory.json',inventory)
    # Stable listing identity is independent of every historical/display name.
    aliases=[];seen=set()
    for s in stockrows:
        if not s['listing_date']:continue
        code=stock_code(s['stock_code']);key=(code,s['listing_date'],s['delisting_date'])
        if key in seen:continue
        seen.add(key);aliases.append(dict(stock_code=code,exchange=code[-2:],security_id=security_id(code,s['listing_date']),
            valid_from=s['listing_date'],valid_to=s['delisting_date'],evidence_id=f"pg_raw:{s['raw_id']}",identity_quality='catalogue_listing_no_unproved_code_alias'))
    alias_by_code=defaultdict(list)
    for a in aliases:alias_by_code[a['stock_code']].append(a)
    timeline=[]
    def add(code,start,end,risk,trading,source,quality,seen_at,eid,name=None):
        code=stock_code(code);sid=resolve_security(code,start,alias_by_code[code]);end_sid=resolve_security(code,end,alias_by_code[code])
        verified=sid is not None and sid==end_sid
        timeline.append(dict(security_id=sid if verified else None,stock_code=code,exchange=code[-2:],valid_from=start,valid_to=end,
            historical_name=name,risk_warning_status=risk if verified else 'unknown',trading_status=trading if verified else 'unknown',
            evidence_source=source,evidence_quality=quality,fetched_at=seen_at,verification_status='source_observed' if verified else 'identity_ambiguous',evidence_id=eid))
    observations=pq.read_table(old/'status_observations.parquet').to_pylist()
    for x in observations:
        add(x['stock_code'],x['trade_date'],x['trade_date'],'unknown' if x['is_st'] is None else 'st' if x['is_st'] else 'none',
            'unknown' if x['trade_status'] is None else 'normal_trading' if x['trade_status'] else 'suspended',
            x['source'],'observed' if x['observed_at_utc'] else 'observed_time_unknown',x['observed_at_utc'],x['evidence_id'])
    for x in eventrows:
        if not x['stock_name']:continue
        add(x['stock_code'],x['trade_date'],x['trade_date'],risk_from_name(x['stock_name']),'unknown',x['provider'],
            'dated_name_attribute_not_complete_name_history',x['fetched_at'].isoformat(),f"pg_raw:{x['raw_id']}",x['stock_name'])
    pq.write_table(pa.Table.from_pylist(aliases),ROOT/'security_aliases.parquet',compression='zstd')
    pq.write_table(pa.Table.from_pylist(timeline),ROOT/'historical_stock_status.parquet',compression='zstd')
    by_code=defaultdict(list)
    for x in timeline:by_code[x['stock_code']].append(x)
    def lookup(code,d):return status_at(code,d,by_code.get(code,[]),alias_by_code.get(code,[]))
    traces=list(csv.DictReader((BASE/'dragon_diagnostics_v051/cycle_exclusions.csv').open()))
    blocked=[]
    for t in traces:
        key='left_boundary_date' if t['left_reason']=='historical_st_missing' else 'right_boundary_date' if t['left_reason']=='established' and t['right_reason']=='historical_st_missing' else None
        if key:blocked.append((t,date.fromisoformat(t[key])))
    cases=json.loads((BASE/'dragon_diagnostics_v051/cases.json').read_text())
    with Research() as r:
        cal=[x['trade_date'] for x in r.query('select trade_date from calendar order by 1')];idx={d:i for i,d in enumerate(cal)}
        stocks={x['stock_code']:x for x in r.query('select * from stocks')}
        codes=sorted({t['stock_code'] for t,_ in blocked})
        lo=min(date.fromisoformat(t['left_boundary_date']) for t,_ in blocked);hi=max(date.fromisoformat(t['right_boundary_date']) for t,_ in blocked)
        prices={(x['stock_code'],x['trade_date']):x for x in r.query('select * from daily where stock_code=ANY(?) and trade_date between ? and ?',[codes,lo,hi])}
        db=duckdb.connect(config={'memory_limit':'512MB','threads':2});db.read_parquet(str(old/'facts_*.parquet')).create_view('facts')
        facts_rows=db.execute('select * from facts where stock_code=ANY(?) and trade_date between ? and ?',[codes,lo,hi]);cols=[x[0] for x in facts_rows.description]
        facts={(x['stock_code'],x['trade_date']):x for x in (dict(zip(cols,row)) for row in facts_rows.fetchall())}
        cf=[];resolved=[]
        for t,boundary in blocked:
            code=t['stock_code'];state=lookup(code,boundary)
            resolved.append(dict(cycle_id=t['cycle_id'],stock_code=code,boundary_date=boundary,**{k:state[k] for k in ('is_st','verification_status','security_id')},resolved=state['is_st'] is not None))
            start=date.fromisoformat(t['left_boundary_date']);end=date.fromisoformat(t['right_boundary_date']);span=cal[idx[start]:idx[end]+1]
            for assumed_st in (False,True):
                diagnostic={};reasons=Counter();strict_known=0
                for d in span:
                    bar=prices.get((code,d));f=facts.get((code,d),{});actual=lookup(code,d);s=stocks.get(code,{})
                    if bar:bar={**bar,'pre_close':f.get('reference_price_used') or bar.get('pre_close')}
                    e=dict(listing_date=s.get('listing_date'),delisting_date=s.get('delisting_date'),listing_session=idx[d]-idx[s['listing_date']]+1 if s.get('listing_date') in idx else None,
                        is_st=actual['is_st'] if actual['is_st'] is not None else assumed_st,
                        trade_status={'normal_trading':True,'suspended':False}.get(actual['trading_status']),
                        special_status='unknown',reference_verified=f.get('reference_verified',False),conflict=actual['verification_status'] in ('conflict','identity_ambiguous'))
                    strict=reconstruct(bar,e);dg=reconstruct(bar,e,diagnostic=True)
                    if strict['classification']=='unknown':reasons[strict['reason']]+=1
                    else:strict_known+=1
                    diagnostic[d]=dict(classification=dg['classification'],in_target=True)
                found=cycles(code,span,diagnostic,{},require_event=False)
                cf.append(dict(cycle_id=t['cycle_id'],stock_code=code,assumed_st=assumed_st,strict_unknown_days=sum(reasons.values()),
                    strict_blocking_reasons=dict(reasons),strict_interval_passed=not reasons,
                    diagnostic_cycle_same_start_complete=any(c['first_observed_date']==date.fromisoformat(t['first_observed_date']) and c['cycle_complete'] for c in found),
                    scenario_only=True,formal_eligible=False))
        save('blocked_693.json',resolved);save('counterfactual/cycles.json',cf)
        case_missing=[];case_summary=[]
        for case in cases:
            code=case['stock_code'];run=case['longest_price_scenario'];start=date.fromisoformat(run['start']);end=date.fromisoformat(run['end']);span=cal[idx[start]-1:idx[end]+2]
            rows=db.execute('select * from facts where stock_code=? and trade_date between ? and ?',[code,span[0],span[-1]]);columns=[x[0] for x in rows.description];mapped={x['trade_date']:x for x in (dict(zip(columns,v)) for v in rows.fetchall())}
            for d in span:
                st=lookup(code,d);f=mapped.get(d,{});missing=[]
                if st['is_st'] is None:missing.append('historical_st')
                if st['trading_status']=='unknown':missing.append('trading_status')
                if not f.get('price_present'):missing.append('unadjusted_daily')
                if not f.get('official_limit_available'):missing.append('independent_limit_price')
                if not f.get('reference_verified'):missing.append('verified_ex_reference')
                if f.get('status','unknown')=='unknown':missing.append('strict_limit_state_and_special_session')
                case_missing.append(dict(stock_code=code,security_id=st['security_id'],trade_date=d,missing_fields=missing,risk_warning_status=st['risk_warning_status'],
                    limit_status=f.get('status','unknown'),historical_name_attributes=[x['historical_name'] for x in by_code[code] if x['valid_from']<=d<=x['valid_to'] and x['historical_name']]))
            subset=[x for x in case_missing if x['stock_code']==code]
            case_summary.append(dict(stock_code=code,window_start=span[0],window_end=span[-1],days=len(span),missing_st_days=sum('historical_st' in x['missing_fields'] for x in subset),
                first_board_date=None,max_height=None,cycle_complete=False,verification_status='unknown',window_selection='previous_price_scenario_for_inspection_only_not_label'))
        pq.write_table(pa.Table.from_pylist(case_missing),ROOT/'case_missing_fields.parquet',compression='zstd');save('cases.json',case_summary)
        counts=db.execute('''select count(*),count(*) filter(where is_st is null),count(*) filter(where not official_limit_available),
            count(*) filter(where not reference_verified),count(*) filter(where diagnostic_reason='invalid_ex_reference'),
            count(*) filter(where reason='special_session_history_unverified'),count(*) filter(where state='suspended') from facts''').fetchone()
        reasons=dict(db.execute("select diagnostic_reason,count(*) from facts where diagnostic_classification='unknown' group by 1").fetchall())
        annual=db.execute('select year(trade_date) as yr,count(*) as n,count(*) filter(where is_st is not null) as known_st from facts group by 1 order by 1').fetchall()
        db.read_parquet(str(ROOT/'historical_stock_status.parquet')).create_view('timeline')
        changes=db.execute("""with resolved as (
            select stock_code,valid_from d,min(risk_warning_status!='none') st,
            count(distinct(risk_warning_status!='none')) variants from timeline
            where risk_warning_status!='unknown' and security_id is not null group by 1,2)
            select count(*) filter(where f.is_st is null and r.variants=1),
            count(*) filter(where f.is_st is not null and f.is_st!=r.st),count(*) filter(where r.variants>1)
            from resolved r join facts f on r.stock_code=f.stock_code and r.d=f.trade_date""").fetchone()
        if any(changes):raise ValueError('Changed ST facts require full price-rule revalidation before using the old coverage snapshot')
        # Re-audit stored cycles: every positive needs complete ST/identity and all
        # preexisting strict evidence. No hypothetical output enters this loop.
        old_cycles=json.loads((old/'cycles.json').read_text());eligible=[]
        for c in old_cycles:
            if not c['cycle_complete']:continue
            dates=[p['trade_date'] for p in c['progress']]+[c['left_boundary_date'],c['right_boundary_date']]
            if c['formal_eligible'] and all(lookup(c['stock_code'],date.fromisoformat(d))['is_st'] is not None for d in dates if d):eligible.append(c['cycle_id'])
        cf_summary={str(st):dict(cycles=len([x for x in cf if x['assumed_st']==st]),strict_intervals_passed=sum(x['strict_interval_passed'] for x in cf if x['assumed_st']==st),
            diagnostic_complete_same_start=sum(x['diagnostic_cycle_same_start_complete'] for x in cf if x['assumed_st']==st),
            remaining_blocked_cycles_by_reason=dict(Counter(k for x in cf if x['assumed_st']==st for k in x['strict_blocking_reasons']))) for st in (False,True)}
    summary=dict(version='dragon-st-v0.5.3',built_at_utc=datetime.now(timezone.utc),timeline_rows=len(timeline),state_observation_rows=len(observations),dated_name_rows=len(eventrows),aliases=len(aliases),
        unique_state_stock_days=len({(x['stock_code'],x['trade_date']) for x in observations}),
        resolved_693=sum(x['resolved'] for x in resolved),remaining_693=sum(not x['resolved'] for x in resolved),
        audited_stock_days=counts[0],st_unknown_stock_days=counts[1],missing_independent_limit_stock_days=counts[2],
        unverified_reference_stock_days=counts[3],invalid_reference_primary_blockers=counts[4],special_history_primary_blockers=counts[5],observed_suspension_stock_days=counts[6],
        diagnostic_unknown_reasons=reasons,annual=[dict(year=y,stock_days=n,known_st=k,complete=False) for y,n,k in annual],
        reaudited_cycles=len(old_cycles),new_verifiable_cycles=len(eligible),new_formal_L1_labels=0,verified_count=0,
        counterfactual=cf_summary,formal_years=[],case_summary=case_summary,
        timeline_vs_production=dict(new_known_st_days=changes[0],different_known_st_days=changes[1],conflicting_st_days=changes[2]),
        production_manifest_sha256=hashlib.sha256((BASE/'dragon_v052/manifest.json').read_bytes()).hexdigest())
    save('summary.json',summary)
    save('manifest.json',{'version':summary['version'],'files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.rglob('*') if p.is_file() and p.name!='manifest.json'}})
    print(json.dumps(summary,default=str,ensure_ascii=False,indent=2))

if __name__=='__main__':audit()
