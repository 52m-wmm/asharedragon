"""v0.5.4 bounded case-first evidence audit. No network, paid calls or price writes."""
from pathlib import Path
from datetime import date,datetime,timezone
from collections import Counter,defaultdict
from urllib.parse import urlsplit
import json,hashlib,re,subprocess
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from app.db.session import engine
from app.research.queries import Research
from app.research.trading_evidence import trading_evidence,limit_evidence,streak_fields,VERSION
from app.research.announcement_evidence import closing_ranges
from app.research.stock_status import resolve_security
from build_us_market_signals import event_snapshot

BASE=Path.home()/'Datasets/asharedragon/research'
ROOT=BASE/'dragon_v054'


def save(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,default=str,ensure_ascii=False,indent=2));tmp.replace(path)


def build():
    ROOT.mkdir(parents=True,exist_ok=True)
    old_manifest=json.loads((BASE/'dragon_v052/manifest.json').read_text());old=BASE/'dragon_v052'/old_manifest['dataset']
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out=ROOT/'snapshots'/stamp;out.mkdir(parents=True)
    cal,pools,source_proof,issues=event_snapshot();idx={d:i for i,d in enumerate(cal)}
    events=defaultdict(list);multiplicity=Counter()
    with engine.connect() as conn,conn.begin():
        conn.execute(text('SET TRANSACTION READ ONLY'))
        raw={r.id:r.raw_payload for r in conn.execute(text("select id,raw_payload from raw_observations where ingestion_status='success' and endpoint='limit_list_d'"))}
        available=[dict(x) for x in conn.execute(text("select endpoint,ingestion_status,count(*) rows from raw_observations where endpoint in ('suspend_d','query_history_k_data_plus','query_daily_history_k_AStock','stk_limit') group by 1,2")).mappings()]
    closes={}
    for rid,payload in raw.items():
        for row in payload.get('records',[]):
            if row.get('ts_code') and row.get('trade_date'):
                closes[rid,row['ts_code'],str(row['trade_date']).replace('-','')]=row.get('close')
    for (provider,d,kind),pool in pools.items():
        for row in pool['rows']:
            code=row['stock_code'];multiplicity[provider,code,d]+=1
            events[code,d].append(dict(stock_code=code,trade_date=d,is_limit_up=kind=='U',trusted=True,
                evidence_id=f"{provider}:raw:{pool['raw_id']}:{pool['hash']}",provider=provider,
                close=closes.get((pool['raw_id'],code,d.strftime('%Y%m%d'))),observed_at_utc=pool['observed_at'].isoformat()))
    docs=BASE/'dragon_diagnostics_v051/sources';manifest=json.loads((docs/'manifest.json').read_text());announcements=[]
    for name,meta in manifest.items():
        pdf=docs/(name+'.pdf')
        if not pdf.exists() or not meta.get('sha256'):continue
        if urlsplit(meta['url']).hostname not in ('static.cninfo.com.cn','disc.static.szse.cn'):raise ValueError('Unapproved notice host')
        if hashlib.sha256(pdf.read_bytes()).hexdigest()!=meta['sha256']:raise ValueError('Notice hash mismatch')
        # Re-extract from the verified PDF; do not trust a mutable cached txt file.
        content=subprocess.run(['pdftotext',str(pdf),'-'],capture_output=True,text=True,check=True,timeout=10).stdout
        m=re.search(r'/(\d{4}-\d{2}-\d{2})/',meta['url'])
        if not m:continue
        pub=date.fromisoformat(m[1]);claims=closing_ranges(content,pub,cal)
        record=dict(source_key=name,source_url=meta['url'],pdf_sha256=meta['sha256'],publication_date=pub,claims=claims,
            parsing_status='explicit_daily_closing_range' if claims else 'no_unambiguous_daily_closing_range',
            reviewed_at_utc=datetime.now(timezone.utc).isoformat())
        announcements.append(record)
        for claim in claims:
            for d in claim['dates']:
                events[claim['stock_code'],d].append(dict(stock_code=claim['stock_code'],trade_date=d,is_limit_up=True,trusted=True,
                    evidence_id=f"issuer_notice:{meta['sha256']}",provider='issuer_notice',observed_at_utc=record['reviewed_at_utc']))
    save(out/'announcement_evidence.json',announcements);save(out/'event_source_evidence.json',source_proof)
    aliases=pq.read_table(BASE/'dragon_v053/security_aliases.parquet').to_pylist();alias_map=defaultdict(list)
    for x in aliases:alias_map[x['stock_code']].append(x)
    case_rows=pq.read_table(BASE/'dragon_v053/case_missing_fields.parquet').to_pylist();case_days=defaultdict(list)
    for row in case_rows:case_days[row['stock_code']].append(row['trade_date'])
    blocked=json.loads((BASE/'dragon_v053/blocked_693.json').read_text())
    baseline_manifest=json.loads((BASE/'dragon_v1/manifest.json').read_text())
    baseline=json.loads((BASE/'dragon_v1'/baseline_manifest['dataset']/'cycles.json').read_text());baseline_byid={c['cycle_id']:c for c in baseline}
    target=set((r['stock_code'],r['trade_date']) for r in case_rows)
    windows={}
    for b in blocked:
        cycle=baseline_byid[b['cycle_id']];start=date.fromisoformat(cycle['first_observed_date']);last=date.fromisoformat(cycle['progress'][-1]['trade_date'])
        span=cal[max(0,idx[start]-1):min(len(cal),idx[last]+2)]
        windows[b['cycle_id']]=span;target.update((b['stock_code'],d) for d in span)
    facts={};case_results=[]
    with Research() as r:
        r.db.register('targets',pa.Table.from_pylist([dict(stock_code=c,trade_date=d) for c,d in sorted(target)]))
        prices={(x['stock_code'],x['trade_date']):x for x in r.query('select p.* from daily p join targets t using(stock_code,trade_date)')}
        r.db.read_parquet(str(old/'facts_*.parquet')).create_view('old_facts')
        old_facts={(x['stock_code'],x['trade_date']):x for x in r.query('select f.* from old_facts f join targets t using(stock_code,trade_date)')}
        stocks={x['stock_code']:x for x in r.query('select * from stocks')}
        # First evaluate the five cases, then only the predefined 693-cycle audit.
        ordered=list(dict.fromkeys([(x['stock_code'],x['trade_date']) for x in case_rows]+sorted(target)))
        for code,d in ordered:
            bar=prices.get((code,d));previous=old_facts.get((code,d),{});stock=stocks.get(code,{})
            sid=resolve_security(code,d,alias_map.get(code,[]))
            t=trading_evidence(code,d,bar,trusted=True,is_session=d in idx,raw_trade_status=previous.get('trade_status'),
                listing_date=stock.get('listing_date'),delisting_date=stock.get('delisting_date'),lifecycle_trusted=sid is not None,
                suspension_ids=previous.get('state_evidence_ids',[]) if previous.get('state')=='suspended' else [])
            if bar and t['state']=='traded_observed':t['source_ids'].append(f"price:{bar['source']}:{code}:{d}")
            ev=dict(listing_date=stock.get('listing_date'),delisting_date=stock.get('delisting_date'),
                listing_session=idx[d]-idx[stock['listing_date']]+1 if stock.get('listing_date') in idx else None,
                is_st=previous.get('is_st'),special_status='unknown',reference_verified=previous.get('reference_verified',False),
                conflict=sid is None or previous.get('state_quality')=='conflict' or any(multiplicity[p,code,d]>1 for p in ('tushare_proxy','akshare')))
            # Same independently matched reference as v0.5.2; original bar is untouched.
            working={**bar,'pre_close':previous.get('reference_price_used') or bar.get('pre_close')} if bar else None
            limit=limit_evidence(working,ev,t,events.get((code,d),[]))
            if limit['basis']=='confirmed_non_trading_state':limit['evidence_ids']=list(t['source_ids'])
            if limit['status']!='unknown':limit['evidence_ids']=sorted(set(limit['evidence_ids']+t['source_ids']+previous.get('reference_evidence_ids',[])))
            facts[code,d]=dict(stock_code=code,security_id=sid,trade_date=d,raw_trade_status=previous.get('trade_status'),is_st=previous.get('is_st'),
                trading=t,limit=limit,status=limit['status'],evidence_ids=limit['evidence_ids'],known_at_utc=limit['known_at_utc'],
                old_limit_status=previous.get('status'),old_rule_reason=previous.get('reason'),
                price_source=bar.get('source') if bar else None,price_present=bar is not None,
                numeric_official_limit_present=previous.get('official_limit_available',False),reference_verified=previous.get('reference_verified',False))
        for code,days in case_days.items():
            days=sorted(days);byday={d:facts[code,d] for d in days};found=streak_fields(code,cal[idx[days[0]]:idx[days[-1]]+1],byday)
            case_results.append(dict(stock_code=code,window_start=days[0],window_end=days[-1],days=len(days),
                trading_counts=dict(Counter(f['trading']['state'] for f in byday.values())),limit_counts=dict(Counter(f['status'] for f in byday.values())),
                st_unknown_days=sum(f['is_st'] is None for f in byday.values()),numeric_limit_unknown_days=sum(not f['numeric_official_limit_present'] for f in byday.values()),
                rows=list(byday.values()),runs=found,
                gate_passed=bool(found) and all(c['exact_cycle_eligible'] for c in found)))
        reviews=[];audit_runs={}
        for b in blocked:
            code=b['stock_code'];span=windows[b['cycle_id']];byday={d:facts[code,d] for d in span};found=streak_fields(code,span,byday)
            boundary=date.fromisoformat(b['boundary_date']);f=facts[code,boundary]
            for run in found:audit_runs[code,run['first_observed_date'],run['last_observed_date']]=run
            reviews.append(dict(cycle_id=b['cycle_id'],stock_code=code,boundary_date=boundary,
                trading_block_resolved=f['trading']['state']=='traded_observed',st_block_resolved=f['is_st'] is not None,
                boundary_limit_confirmed=f['status']!='unknown',next_rule_reason=f['limit']['rule_reason'],
                exact_cycle_restored=any(x['exact_cycle_eligible'] and str(x['first_observed_date'])==baseline_byid[b['cycle_id']]['first_observed_date'] for x in found)))
        good="isfinite(d.open) AND isfinite(d.high) AND isfinite(d.low) AND isfinite(d.close) AND d.low>0 AND d.high>=greatest(d.open,d.close) AND d.low<=least(d.open,d.close) AND isfinite(d.volume) AND d.volume>0 AND isfinite(d.amount) AND d.amount>0 AND d.amount/d.volume BETWEEN d.low-.01 AND d.high+.01 AND c.trade_date IS NOT NULL"
        totals=r.query(f'''select count(*) price_rows,count(*) filter(where {good}) trusted_traded_bars,
            count(*) filter(where {good} and f.trade_status is null) traded_raw_status_missing
            from daily d join old_facts f using(stock_code,trade_date) left join calendar c using(trade_date)''')[0]
    case_runs=[run for c in case_results for run in c['runs']]
    def label_counts(runs):return {str(n):sum(x['fields'][f'observed_streak_ge_{n}']['value'] is True for x in runs) for n in (3,5,7,10)}
    summary=dict(version=VERSION,built_at_utc=datetime.now(timezone.utc),price_audit=totals,
        historical_state_observations=75461,case_count=len(case_results),case_days=len(case_rows),case_limit_counts=dict(Counter(facts[x['stock_code'],x['trade_date']]['status'] for x in case_rows)),
        blocked_cycles=len(reviews),trading_blocks_resolved=sum(x['trading_block_resolved'] for x in reviews),st_blocks_resolved=sum(x['st_block_resolved'] for x in reviews),
        exact_cycles_restored=sum(x['exact_cycle_restored'] for x in reviews),next_rule_reasons=dict(Counter(x['next_rule_reason'] for x in reviews)),
        case_confirmed_lower_bound_counts=label_counts(case_runs),
        case_lower_bound_only_runs=sum(any(x['fields'][f'observed_streak_ge_{n}']['value'] is True for n in (3,5,7,10)) and x['fields']['exact_max_streak']['value'] is None for x in case_runs),
        audit_693_lower_bound_counts=label_counts(list(audit_runs.values())),
        audit_693_lower_bound_only_runs=sum(any(x['fields'][f'observed_streak_ge_{n}']['value'] is True for n in (3,5,7,10)) and x['fields']['exact_max_streak']['value'] is None for x in audit_runs.values()),
        verified_exact_cycle_labels=sum(x['exact_cycle_eligible'] for x in case_runs),formal_whole_market_L1_labels=0,
        full_market_recalculated=False,case_gate_all_passed=all(x['gate_passed'] for x in case_results),formal_statistical_intervals=[],
        source_coverage=available,event_issues=issues,production_snapshot=str(old),
        production_manifest_sha256=hashlib.sha256((BASE/'dragon_v052/manifest.json').read_bytes()).hexdigest(),
        warning='Field-level confirmations are independent; absent endpoints and left boundaries stay unknown. Cases and 693 audit cohort are separate.')
    save(out/'cases.json',case_results);save(out/'blocked_693.json',reviews);save(out/'audit_693_runs.json',list(audit_runs.values()));save(out/'summary.json',summary)
    pq.write_table(pa.Table.from_pylist(list(facts.values())),out/'stock_day_evidence.parquet',compression='zstd')
    save(ROOT/'manifest.json',dict(dataset=str(out.relative_to(ROOT)),version=VERSION,files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()}))
    print(json.dumps(summary,default=str,ensure_ascii=False,indent=2))

if __name__=='__main__':build()
