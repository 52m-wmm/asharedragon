"""Read-only full-price coverage scan and local-event cycle recovery. No data APIs."""
from collections import Counter,defaultdict
from datetime import date,datetime,timezone
from pathlib import Path
import hashlib,json
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from app.db.session import engine
from app.research.queries import Research
from app.research.stock_status import resolve_security
from app.research.trading_evidence import trading_evidence,limit_evidence,streak_fields
from app.research.coverage_v056 import VERSION,first_board_sample,boundary_gaps
from app.research.dragon_queries import distribution
from build_us_market_signals import event_snapshot
from build_streak_facts import load_snapshot,save

BASE=Path.home()/'Datasets/asharedragon/research';ROOT=BASE/'dragon_v056'


def build():
    now=datetime.now(timezone.utc);out=ROOT/'snapshots'/now.strftime('%Y%m%dT%H%M%S%fZ');out.mkdir(parents=True)
    prior,prior_manifest=load_snapshot(BASE/'dragon_facts');old_rows=json.loads((prior/'facts.json').read_text())
    oldp,_=load_snapshot(BASE/'dragon_v054');rules=json.loads((ROOT/'rules/manifest.json').read_text())
    for rule in rules:
        if hashlib.sha256(Path(rule['file']).read_bytes()).hexdigest()!=rule['sha256']:raise ValueError('Rule file changed')
    rule_case='CASE '+''.join(f"WHEN d.trade_date BETWEEN DATE '{x['start']}' AND DATE '{x['end']}' AND ({' OR '.join('starts_with(d.stock_code, '+repr(p)+')' for p in x['prefixes'])}) THEN '{x['id']}' " for x in rules)+"ELSE NULL END"
    cal,pools,pool_evidence,issues=event_snapshot();cal=[d for d in cal if d>=date(2017,1,1)];idx={d:i for i,d in enumerate(cal)}
    events=defaultdict(list);features=defaultdict(list);counts=Counter()
    with engine.connect() as conn,conn.begin():
        conn.execute(text('SET TRANSACTION READ ONLY'))
        raw={x.id:x.raw_payload for x in conn.execute(text("select id,raw_payload from raw_observations where ingestion_status='success' and endpoint='limit_list_d'"))}
        source_inventory=[dict(x) for x in conn.execute(text("select endpoint,count(*) raw_responses from raw_observations where ingestion_status='success' and endpoint in ('stk_limit','stock_st','namechange','suspend_d','query_history_k_data_plus') group by 1")).mappings()]
    closes={(rid,x['ts_code'],str(x['trade_date']).replace('-','')):x.get('close') for rid,payload in raw.items() for x in payload.get('records',[]) if x.get('ts_code') and x.get('trade_date')}
    for (provider,d,kind),pool in pools.items():
        if d not in idx:continue
        for row in pool['rows']:
            code=row['stock_code'];counts[provider,code,d]+=1
            eid=f"{provider}:raw:{pool['raw_id']}:{pool['hash']}"
            events[code,d].append(dict(stock_code=code,trade_date=d,is_limit_up=kind=='U',trusted=True,evidence_id=eid,
                close=closes.get((pool['raw_id'],code,d.strftime('%Y%m%d'))),observed_at_utc=pool['observed_at'].isoformat()))
            if kind=='U':features[code,d].append(dict(source=provider,evidence_id=eid,fetched_at=pool['observed_at'],
                circulating_market_cap=float(row['circulating_market_cap']) if row.get('circulating_market_cap') is not None else None,
                turnover_rate=float(row['turnover_rate']) if row.get('turnover_rate') is not None else None))
    for notice in json.loads((oldp/'announcement_evidence.json').read_text()):
        for claim in notice['claims']:
            for ds in claim['dates']:
                d=date.fromisoformat(ds);code=claim['stock_code']
                events[code,d].append(dict(stock_code=code,trade_date=d,is_limit_up=True,trusted=True,evidence_id='issuer_notice:'+notice['pdf_sha256'],observed_at_utc=notice['reviewed_at_utc']))
    aliases=pq.read_table(BASE/'dragon_v053/security_aliases.parquet').to_pylist();am=defaultdict(list)
    for a in aliases:am[a['stock_code']].append(a)
    old_manifest=json.loads((BASE/'dragon_v052/manifest.json').read_text());old=BASE/'dragon_v052'/old_manifest['dataset']
    direct=[]
    with Research() as r:
        r.db.read_parquet(str(old/'facts_*.parquet')).create_view('old_facts')
        r.db.register('event_keys',pa.Table.from_pylist([dict(stock_code=c,trade_date=d) for c,d in events]))
        bars={(x['stock_code'],x['trade_date']):x for x in r.query('select d.* from daily d join event_keys using(stock_code,trade_date)')}
        previous={(x['stock_code'],x['trade_date']):x for x in r.query('select f.* from old_facts f join event_keys using(stock_code,trade_date)')}
        stocks={x['stock_code']:x for x in r.query('select * from stocks')}
        for (code,d),evs in events.items():
            bar=bars.get((code,d));prev=previous.get((code,d),{});st=stocks.get(code,{})
            sid=resolve_security(code,d,am.get(code,[]))
            t=trading_evidence(code,d,bar,trusted=True,is_session=d in idx,raw_trade_status=prev.get('trade_status'),
                listing_date=st.get('listing_date'),delisting_date=st.get('delisting_date'),lifecycle_trusted=sid is not None)
            ev=dict(conflict=sid is None or prev.get('state_quality')=='conflict' or any(counts[p,code,d]>1 for p in ('tushare_proxy','akshare')),
                is_st=prev.get('is_st'),listing_date=st.get('listing_date'),delisting_date=st.get('delisting_date'))
            f=limit_evidence(bar,ev,t,evs)
            # Conflicting explicit event prices and source labels stay quarantined.
            direct.append(dict(stock_code=code,trade_date=d,direct_status=f['status'],direct_quality=f['quality'],
                direct_reason=f['basis'] if f['status']!='unknown' else 'direct_event_conflict_or_unusable_price',
                evidence_ids=f['evidence_ids'],known_at_utc=f['known_at_utc']))
        r.db.register('direct',pa.Table.from_pylist(direct))
        # One security identity and one row per stock-day are mandatory for SQL-negative proof.
        r.db.register('aliases',pa.Table.from_pylist(aliases))
        r.db.sql("select stock_code,min(valid_from) first_listed,max(valid_to) last_end,count(distinct security_id) identities,count(*) alias_rows,bool_or(valid_to is null) open_ended from aliases group by 1").create_view('identity_scope')
        valid=" AND ".join([f'isfinite(d.{k}) AND d.{k}>0 AND abs(d.{k}-round(d.{k},2))<1e-9' for k in ('open','high','low','close')])+" AND d.low<=least(d.open,d.close) AND d.high>=greatest(d.open,d.close) AND isfinite(d.volume) AND d.volume>0 AND isfinite(d.amount) AND d.amount>0 AND d.amount/d.volume BETWEEN d.low-.01 AND d.high+.01 AND d.adjustment_type='none' AND c.trade_date IS NOT NULL AND f.trade_status IS DISTINCT FROM false AND f.state_quality IS DISTINCT FROM 'conflict' AND a.identities=1 AND a.alias_rows=1 AND d.trade_date>=a.first_listed AND (a.open_ended OR d.trade_date<=a.last_end)"
        r.db.sql(f'''SELECT d.stock_code,d.trade_date,d.close,d.high,d.source,d.volume,d.amount,
            coalesce(({valid}),false) valid, {rule_case} rule_id,
            f.is_st,f.trade_status,f.reason old_reason,f.official_limit_available,f.reference_verified,
            e.direct_status,e.direct_quality,e.evidence_ids,e.known_at_utc
            FROM daily d LEFT JOIN old_facts f USING(stock_code,trade_date)
            LEFT JOIN calendar c USING(trade_date) LEFT JOIN identity_scope a USING(stock_code)
            LEFT JOIN direct e USING(stock_code,trade_date)''').create_view('classified_base')
        r.db.sql('''SELECT *, CASE WHEN direct_quality='conflict' THEN 'unknown'
            WHEN direct_status IN ('confirmed_limit_up','confirmed_not_limit_up') THEN direct_status
            WHEN valid AND rule_id IS NOT NULL AND close<high THEN 'confirmed_not_limit_up' ELSE 'unknown' END status,
            CASE WHEN direct_quality='conflict' THEN 'source_conflict'
            WHEN direct_status IN ('confirmed_limit_up','confirmed_not_limit_up') THEN 'direct_closing_event'
            WHEN NOT valid THEN 'invalid_price_trading_or_identity'
            WHEN rule_id IS NOT NULL AND close<high THEN 'close_below_session_high'
            WHEN rule_id IS NULL THEN 'negative_rule_scope_not_reviewed'
            ELSE 'no_direct_event_or_official_limit_at_close_equals_high' END reason
            FROM classified_base''').create_view('scan')
        years=r.query('''SELECT year(trade_date)::INTEGER AS "year",count(*) scanned_stock_days,count(distinct trade_date) scanned_sessions,
            count(*) FILTER(WHERE status='confirmed_limit_up') confirmed_up,
            count(*) FILTER(WHERE status='confirmed_not_limit_up') confirmed_not_up,
            count(*) FILTER(WHERE status='unknown') unknown_days,
            count(*) FILTER(WHERE is_st IS NULL) st_unknown,
            count(*) FILTER(WHERE NOT coalesce(official_limit_available,false)) official_limit_missing,
            count(*) FILTER(WHERE NOT coalesce(reference_verified,false)) reference_unverified
            FROM scan GROUP BY 1 ORDER BY 1''')
        reasons=r.query("select year(trade_date)::INTEGER AS year,reason,count(*) stock_days from scan where status='unknown' group by 1,2 order by 1,3 desc")
        target=set()
        for x in direct:
            c,d=x['stock_code'],x['trade_date'];target.update((c,t) for t in cal[max(0,idx[d]-1):idx[d]+6])
        for row in old_rows:
            a=date.fromisoformat(row['first_observed_date']);b=date.fromisoformat(row['last_observed_date'])
            target.update((row['stock_code'],t) for t in cal[max(0,idx[a]-1):idx[b]+6])
        r.db.register('target',pa.Table.from_pylist([dict(stock_code=c,trade_date=d) for c,d in sorted(target)]))
        local=r.query('select s.* from scan s join target using(stock_code,trade_date) order by stock_code,trade_date')
        expected=r.query('''select year(c.trade_date)::INTEGER AS "year",count(*) listed_calendar_stock_days from calendar c join stocks s
            on c.trade_date>=s.listing_date and (s.delisting_date is null or c.trade_date<=s.delisting_date)
            where c.trade_date between '2017-01-01' and '2026-11-06' group by 1 order by 1''')
        day_coverage=r.query("select trade_date,count(*) total,count(*)filter(where status!='unknown') known,count(*)filter(where status='confirmed_limit_up') ups from scan group by 1")
    # No price copies: minimal evidence only in the derived stock-day layer.
    facts=defaultdict(dict)
    for x in local:
        ids=list(x['evidence_ids'] or [])
        if x['status']!='unknown':ids.append(f"price:{x['source']}:{x['stock_code']}:{x['trade_date']}")
        if x['reason']=='close_below_session_high':ids.append(x['rule_id'])
        facts[x['stock_code']][x['trade_date']]=dict(stock_code=x['stock_code'],trade_date=x['trade_date'],status=x['status'],reason=x['reason'],
            evidence_ids=sorted(set(ids)),known_at_utc=now.isoformat() if x['reason']=='close_below_session_high' else x['known_at_utc'],
            is_st=x['is_st'],raw_trade_status=x['trade_status'])
    for c,d in target:
        if d not in facts[c]:facts[c][d]=dict(stock_code=c,trade_date=d,status='unknown',reason='missing_stock_day',evidence_ids=[],known_at_utc=None)
    rows=[];samples=[]
    for code,byday in facts.items():
        span=cal[idx[min(byday)]:idx[max(byday)]+1]
        for run in streak_fields(code,span,byday):
            fields=run['fields'];gaps=boundary_gaps(run,cal,byday)
            rid=f"observed:{code}:{run['first_observed_date']}:{run['last_observed_date']}"
            row=dict(fact_id=rid,stock_code=code,cohort='all_local_direct_events',first_observed_date=run['first_observed_date'],last_observed_date=run['last_observed_date'],
                observed_min_height=run['observed_length'],exact_max_streak=fields['exact_max_streak']['value'],first_board_date=fields['first_board_date']['value'],cycle_end_date=fields['cycle_end_date']['value'],
                fields=fields,progress=run['progress'],complete_cycle_eligible=run['exact_cycle_eligible'],evidence_status='confirmed_complete_cycle' if run['exact_cycle_eligible'] else 'confirmed_lower_bound',
                blockers=gaps,label_version=VERSION,evidence_ids=sorted({e for f in fields.values() for e in f['evidence_ids']}),publication_blockers=['year_population_coverage_incomplete'],retrospective=True)
            rows.append(row);sample=first_board_sample(run,cal,byday);sample.update(cycle_id=rid,first_observed_date=run['first_observed_date'],complete_cycle_eligible=run['exact_cycle_eligible'],observed_min_height=run['observed_length'],exact_max_streak=row['exact_max_streak'],blockers=gaps)
            src=features.get((code,sample['first_board_date']),[])
            for k in ('circulating_market_cap','turnover_rate'):
                vals={s[k] for s in src if s[k] is not None};sample[k]=next(iter(vals)) if len(vals)==1 else None
            sample.update(feature_provenance=src,feature_asof='first-board EOD historical revision; actual historical publication time not verified',market_environment=None,market_environment_reason='full_market_sentiment_coverage_unverified')
            samples.append(sample)
    rows.sort(key=lambda x:(-x['observed_min_height'],x['stock_code'],x['first_observed_date']))
    recovery=[]
    for prev in old_rows:
        if prev['complete_cycle_eligible']:continue
        match=[r for r in rows if r['stock_code']==prev['stock_code'] and str(r['first_observed_date'])<=prev['first_observed_date'] and str(r['last_observed_date'])>=prev['last_observed_date']]
        r=match[0] if len(match)==1 else None
        recovery.append(dict(previous_fact_id=prev['fact_id'],stock_code=prev['stock_code'],restored=bool(r and r['complete_cycle_eligible']),
            current_fact_id=r['fact_id'] if r else None,observed_min_height=r['observed_min_height'] if r else None,
            exact_max_streak=r['exact_max_streak'] if r else None,first_board_date=r['first_board_date'] if r else None,cycle_end_date=r['cycle_end_date'] if r else None,
            blockers=r['blockers'] if r else [{'reason':'previous_sequence_not_reconfirmed'}]))
    expected={x['year']:x['listed_calendar_stock_days'] for x in expected}
    for y in years:
        yr=y['year'];rs=[x for x in rows if x['first_observed_date'].year==yr];ss=[x for x in samples if (x['first_board_date'] or date.fromisoformat(x['cycle_id'].split(':')[-2])).year==yr]
        categories=Counter(x['category'] for x in ss)
        y.update(listed_calendar_stock_days=expected.get(yr),classified_fraction=(y['confirmed_up']+y['confirmed_not_up'])/y['scanned_stock_days'],
            raw_rows_per_listed_calendar_day=y['scanned_stock_days']/expected[yr] if expected.get(yr) else None,
            first_board_candidates=len(rs),confirmed_first_boards=sum(x['first_board_date'] is not None for x in rs),
            complete_cycles=sum(x['complete_cycle_eligible'] for x in rs),incomplete_segments=sum(not x['complete_cycle_eligible'] for x in rs),
            exact_ge={str(n):sum(x['complete_cycle_eligible'] and x['exact_max_streak']>=n for x in rs) for n in (5,7,10)},
            observed_ge={str(n):sum(x['observed_min_height']>=n for x in rs) for n in (5,7,10)},
            sample_categories=dict(categories),formal_seasonality_eligible=False,population_promotion_rate=None,
            diagnostic_resolved_rate=categories['confirmed_success_ge5']/(categories['confirmed_success_ge5']+categories['confirmed_failure_below5']) if categories['confirmed_success_ge5']+categories['confirmed_failure_below5'] else None,
            unknown_reasons=[x for x in reasons if x['year']==yr],event_source_sessions=len({d for _,d,_ in pools if d.year==yr}),
            first_board_outcome_resolution=sum(x['outcome_eligible'] for x in ss)/sum(x['first_board_date'] is not None for x in ss) if any(x['first_board_date'] for x in ss) else None)
    for y in years:
        c=y['sample_categories'];known=c.get('confirmed_success_ge5',0)+c.get('confirmed_failure_below5',0)+c.get('outcome_unknown',0)
        y['promotion_censoring_bounds']=[c.get('confirmed_success_ge5',0)/known,(c.get('confirmed_success_ge5',0)+c.get('outcome_unknown',0))/known] if known else None
        y['local_research_samples']=sum(s['local_research_eligible'] and s['first_board_date'].year==y['year'] for s in samples)
    monthly=[]
    for yr in range(2017,2027):
        for mo in range(1,13):
            rr=[r for r in rows if r['first_board_date'] and r['first_board_date'].year==yr and r['first_board_date'].month==mo]
            monthly.append(dict(year=yr,month=mo,confirmed_local_first_boards=len(rr),confirmed_local_leaders_ge5=sum(r['complete_cycle_eligible'] and r['exact_max_streak']>=5 for r in rr),formal_population_count=None,eligible=False))
    feature_stats=[]
    for category in ('confirmed_success_ge5','confirmed_failure_below5'):
        ss=[s for s in samples if s['category']==category and s['local_research_eligible']]
        feature_stats.append(dict(category=category,samples=len(ss),capital=distribution([s['circulating_market_cap'] for s in ss]),turnover=distribution([s['turnover_rate'] for s in ss]),formal_eligible=False))
    blocks=Counter(g['reason'] for row in rows for g in row['blockers'])
    summary=dict(version=VERSION,built_at_utc=now,prior_snapshot=str(prior),prior_manifest_sha256=hashlib.sha256((BASE/'dragon_facts/manifest.json').read_bytes()).hexdigest(),
        scanned_stock_days=sum(y['scanned_stock_days'] for y in years),recovered_47=sum(x['restored'] for x in recovery),remaining_47=sum(not x['restored'] for x in recovery),
        cycles=len(rows),complete_cycles=sum(x['complete_cycle_eligible'] for x in rows),complete_leaders_ge5=sum(x['complete_cycle_eligible'] and x['exact_max_streak']>=5 for x in rows),
        observed_segments_ge3=sum(x['observed_min_height']>=3 for x in rows),threshold_counts={str(n):sum(x['observed_min_height']>=n for x in rows) for n in (3,5,7,10)},
        sample_categories=dict(Counter(s['category'] for s in samples)),local_research_categories=dict(Counter(s['category'] for s in samples if s['local_research_eligible'])),source_sessions=len({d for _,d,_ in pools}),source_event_rows=sum(len(p['rows']) for p in pools.values()),
        source_inventory=source_inventory,source_issues=issues,shared_gap_priorities=[{'reason':k,'affected_boundaries':v} for k,v in blocks.most_common()],
        formal_years=[],feature_distributions=feature_stats,market_environment_groups=[],market_environment_reason='Full-market historical sentiment not certified',
        rule_evidence=rules,scope='All audited daily rows plus all usable local direct closing events; missing events are not negatives',
        coverage_denominator='Classified stock-days / scanned raw rows, not historical universe completeness',window_sessions=5)
    save(out/'summary.json',summary);save(out/'years.json',years);save(out/'monthly.json',monthly);save(out/'facts.json',rows);save(out/'samples.json',samples);save(out/'recovery_47.json',recovery);save(out/'sources.json',pool_evidence)
    pq.write_table(pa.Table.from_pylist([f for ds in facts.values() for f in ds.values()]),out/'stock_day_evidence.parquet',compression='zstd')
    save(ROOT/'manifest.json',dict(dataset=str(out.relative_to(ROOT)),version=VERSION,files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()}))
    print(json.dumps({k:summary[k] for k in ('scanned_stock_days','recovered_47','remaining_47','cycles','complete_cycles','complete_leaders_ge5','observed_segments_ge3','threshold_counts','sample_categories')},default=str,ensure_ascii=False,indent=2))

if __name__=='__main__':build()
