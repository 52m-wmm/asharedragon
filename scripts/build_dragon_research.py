"""Offline v0.5 diagnostics. Existing audits/rules are reused; no source writes/network."""
from collections import defaultdict,Counter
from calendar import monthrange
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
import hashlib,json,statistics
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from app.db.session import engine
from app.research.queries import Research
from app.research.dragons import cycles,window_features,eod,VERSION,wilson,research_state
from build_us_market_signals import event_snapshot  # existing read-only local event adapter, no US fetch

ROOT=Path.home()/'Datasets/asharedragon/research/dragon_v1'

def dump(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,default=str,allow_nan=False,indent=2))


def build():
    validation=ROOT/'input_validation';gate=json.loads((validation/'validation.json').read_text())
    if gate['formal_label_count'] or gate['rule_gate']!='blocked_missing_special_history_and_independent_validation':
        raise ValueError('Changed validation gate requires explicit reconciliation before new release')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');out=ROOT/'snapshots'/stamp;out.mkdir(parents=True)
    calendar,pools,proof,issues=event_snapshot()
    calendar=[d for d in calendar if d>=date(2017,1,1)]
    pool_ok={};cycle_pool_ok={};bad_codes={};event_map={};sentiment={}
    for provider,day,kind in pools:
        u=pools.get((provider,day,'U'));z=pools.get((provider,day,'Z'))
        rows=(u or {}).get('rows',[])+(z or {}).get('rows',[])
        codes=[r['stock_code'] for r in rows]
        ok=bool(u and z and len(codes)==len(set(codes)))
        pool_ok[provider,day]=ok
        cycle_pool_ok[provider,day]=bool(u and z)
        bad_codes[provider,day]={code for code,n in Counter(codes).items() if n>1}
        if u and z:
            if any(date.fromisoformat(s['start'])<=day<=date.fromisoformat(s['end']) and s['sample'].startswith('2020' if provider=='tushare_proxy' else '2026') for s in gate['samples']):
                if gate['source_snapshot_hashes'].get(str(u['raw_id']))!=u['hash']:raise ValueError('Event snapshot changed since rule audit')
            for r in u['rows']:
                if r['stock_code'] not in bad_codes[provider,day]:event_map[provider,r['stock_code'],day]={**r,'observed_at':u['observed_at'],'raw_id':u['raw_id']}
        if ok:
            heights=[r['consecutive_limit_days'] for r in u['rows']]
            sentiment[provider,day]={'limit_up_count':len(u['rows']),
                'market_max_height':max(heights) if heights and all(h is not None for h in heights) else None,
                'source':'provider_reported_height_not_independent_reconstruction','observed_at':max(u['observed_at'],z['observed_at'])}
    facts=pq.read_table(validation/'diagnostic_stock_days.parquet').to_pylist()
    grouped=defaultdict(dict)
    for f in facts:
        if f['in_target'] and (not cycle_pool_ok.get((f['provider'],f['trade_date'])) or f['stock_code'] in bad_codes.get((f['provider'],f['trade_date']),set())):
            f={**f,'classification':'unknown','reason':'incomplete_or_conflicting_pool'}
        grouped[f['sample'],f['provider'],f['stock_code']][f['trade_date']]=f
    samples={s['sample']:s for s in gate['samples']};all_cycles=[]
    for (sample,provider,code),byday in grouped.items():
        if not any(f['in_target'] and (provider,code,d) in event_map for d,f in byday.items()):continue
        span=[d for d in calendar if min(byday)<=d<=date.fromisoformat(samples[sample]['end'])]
        events={d:event_map[provider,code,d] for d in span if (provider,code,d) in event_map}
        found=cycles(code,span,byday,events)
        represented={p['trade_date'] for c in found for p in c['progress']}
        for d,event in events.items():
            if d not in represented and byday.get(d,{}).get('in_target'):
                found.append(dict(stock_code=code,first_observed_date=d,first_board_date=None,left_boundary_verified=False,
                    observed_height=1,progress=[dict(trade_date=d,height_asof=None,provider_height=event['consecutive_limit_days'],available_proxy_utc=eod(d),source_observed_at_utc=event['observed_at'])],
                    cycle_complete=False,right_boundary_date=None,max_height=None,outcome_known_at_utc=None,
                    quality='unverified_event_observation',label_version=VERSION,formal_eligible=False,
                    **{f'reached_{n}':None for n in (1,3,5,7,10)}))
        for c in found:
            c.update(sample=sample,provider=provider,cycle_id=f"{sample}:{code}:{c['first_observed_date']}")
            all_cycles.append(c)
    with engine.connect() as conn,conn.begin():
        conn.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        valuations=[dict(r) for r in conn.execute(text("SELECT d.stock_code,d.trade_date,d.pe_ttm,d.pb,d.raw_id,r.fetched_at FROM stock_daily d JOIN raw_observations r ON d.raw_id=r.id WHERE d.provider='baostock' AND d.adjustment='none' ORDER BY d.id")).mappings()]
    value_map={(r['stock_code'],r['trade_date']):r for r in valuations}
    codes=sorted({c['stock_code'] for c in all_cycles});daily={}
    with Research() as research:
        counts=research.query('SELECT count(*) rows,count(DISTINCT stock_code) stocks,min(trade_date) first,max(trade_date) last,count(is_st) st_rows FROM daily')[0]
        for lo,hi in [('2020-10-01','2020-12-31'),('2026-09-01','2026-11-04')]:
            for r in research.query('SELECT stock_code,trade_date,open,high,low,close,volume,amount,pre_close,source,adjustment_type FROM daily WHERE stock_code=ANY(?) AND trade_date BETWEEN ? AND ?',[codes,lo,hi]):daily[r['stock_code'],r['trade_date']]=r
        gap=research.query("SELECT count(*) n,count(*) FILTER(WHERE trade_date>='2026-03-16') recent FROM read_parquet(?)",[str(research.root/'research/missing_stock_dates.parquet')])[0]
        gap_pairs={(r['stock_code'],r['trade_date']) for r in research.query('SELECT stock_code,trade_date FROM read_parquet(?)',[str(research.root/'research/missing_stock_dates.parquet')])}
        date_coverage=research.query('SELECT extract(year FROM trade_date)::INT AS year,count(DISTINCT trade_date) sessions,count(*) rows,count(DISTINCT stock_code) stocks FROM daily GROUP BY 1 ORDER BY 1')
        catalogue=research.catalog
    for c in all_cycles:
        day=c['first_board_date'];event=event_map.get((c['provider'],c['stock_code'],day),{})
        c['stock_name']=event_map.get((c['provider'],c['stock_code'],c['first_observed_date']),{}).get('stock_name')
        c['industry']=None;c['theme']=None;c['theme_source']=None
        c['feature_source']=c['provider'];c['feature_observed_at_utc']=event.get('observed_at')
        c['feature_available_proxy_utc']=eod(day) if day else None;c['feature_pit_verified']=False
        c['circulating_market_cap']=float(event['circulating_market_cap']) if event.get('circulating_market_cap') is not None else None
        c['turnover_rate']=float(event['turnover_rate']) if event.get('turnover_rate') is not None else None
        c['amount']=float(event['amount']) if event.get('amount') is not None else None
        c['first_limit_time']=str(event['first_limit_time']) if event.get('first_limit_time') else None
        c['open_times']=event.get('open_times');valuation=value_map.get((c['stock_code'],day),{})
        c['pe_ttm']=valuation.get('pe_ttm');c['pb']=valuation.get('pb')
        price=daily.get((c['stock_code'],day),{})
        c['board_type']='one_price' if price and price['open']==price['high']==price['low']==price['close'] else 'traded_range' if price else None
        # A zero-range EOD bar establishes shape, never queue execution or intraday sequence.
        c['market']=sentiment.get((c['provider'],day))
        c['windows']={};c['candles']=[]
        c['daily_gap_dates']=[p['trade_date'] for p in c['progress'] if (c['stock_code'],p['trade_date']) in gap_pairs]
        if day:
            idx=calendar.index(day);before=calendar[max(0,idx-21):idx]
            rows=[{'trade_date':d,**daily.get((c['stock_code'],d),{})} for d in before]
            c['windows']={str(n):window_features(rows,n) for n in (5,10,20)}
            c['window_gap_dates']=[d for d in before if (c['stock_code'],d) in gap_pairs]
            end=c['right_boundary_date'] or c['progress'][-1]['trade_date']
            c['candles']=[{'trade_date':d,**daily.get((c['stock_code'],d),{})} for d in calendar[max(0,idx-20):calendar.index(end)+1]]
        c['feature_provenance']={
            'event_fields':{'source':c['provider'],'raw_id':event.get('raw_id'),'observed_at_utc':event.get('observed_at'),'available_proxy_utc':c['feature_available_proxy_utc'],'money_unit':'CNY','turnover_unit':'percent','time_zone':'Asia/Shanghai'},
            'valuation':{'source':'baostock' if valuation else None,'raw_id':valuation.get('raw_id'),'observed_at_utc':valuation.get('fetched_at'),'available_proxy_utc':c['feature_available_proxy_utc'],'null_reason':None if valuation else 'no_same_date_verified_valuation'},
            'windows':{'source':sorted({r['source'] for r in c['candles'] if r.get('source')}),'observed_at_utc':catalogue['snapshot_at'],'available_proxy_utc':eod(before[-1]) if day and before else None,'basis':'raw prices; pre_close bridge checked; factors prohibited; not certified total returns'},
            'industry_theme':{'source':None,'reason':'no_certified_effective_dated_history'},
            'market':c['market'],'availability':'EOD proxy is not actual publication evidence; historical download is not a PIT snapshot'}
    for c in all_cycles:
        c['research_state']=research_state(c,publication_accepted=False)
        c['max_height_state']='diagnostic' if c['cycle_complete'] else 'unknown'
    all_cycles.sort(key=lambda c:(c['first_observed_date'],c['stock_code']))
    months=[]
    for y in range(2017,2027):
        for m in range(1,13):
            days=[d for d in calendar if d.year==y and d.month==m]
            if not days:continue
            covered=sum(any(pool_ok.get((p,d)) for p in ('tushare_proxy','akshare')) for d in days)
            interval_days=sum(any(date.fromisoformat(s['start'])<=d<=date.fromisoformat(s['end']) for s in samples.values()) for d in days)
            months.append(dict(year=y,month=m,expected_sessions=len(days),event_sessions=covered,rule_audit_sessions=interval_days,
                event_coverage=covered/len(days),audit_coverage=interval_days/len(days),full_month_events=covered==len(days) and date(y,m,monthrange(y,m)[1])<=calendar[-1],
                full_month_audit=interval_days==len(days) and date(y,m,monthrange(y,m)[1])<=calendar[-1],strict_accepted=False))
            months[-1]['by_provider']={}
            for provider in ('akshare','tushare_proxy'):
                source_days=sum(pool_ok.get((provider,d),False) for d in days)
                audit_days=sum(any(date.fromisoformat(s['start'])<=d<=date.fromisoformat(s['end']) and s['sample'].startswith('2020' if provider=='tushare_proxy' else '2026') for s in samples.values()) for d in days)
                months[-1]['by_provider'][provider]=dict(event_sessions=source_days,event_coverage=source_days/len(days),rule_audit_sessions=audit_days,
                    audit_coverage=audit_days/len(days),full_month_events=source_days==len(days) and date(y,m,monthrange(y,m)[1])<=calendar[-1],
                    full_month_audit=audit_days==len(days) and date(y,m,monthrange(y,m)[1])<=calendar[-1],strict_accepted=False)

    meta=dict(label_version=VERSION,rule_version=gate['version'],built_at_utc=datetime.now(timezone.utc),formal_count=0,
        strict_accepted=False,rule_gate=gate['rule_gate'],diagnostic_count=len(all_cycles),
        complete_cycles=sum(c['cycle_complete'] for c in all_cycles),unknown_cycles=sum(not c['cycle_complete'] for c in all_cycles),
        verified_first_boards=sum(c['first_board_date'] is not None for c in all_cycles),
        independent_limit_rows=gate['independent_price_rows'],daily=counts,missing_stock_days=gap,
        isolated_event_stock_days=[{'provider':p,'trade_date':d,'stock_codes':sorted(c)} for (p,d),c in bad_codes.items() if c],
        st_known_fraction=counts['st_rows']/counts['rows'],yearly_daily=date_coverage,event_issues=issues,
        intervals=[{k:s[k] for k in ('sample','start','end','sessions')} for s in samples.values()],
        sources=['purchased_usb_0dfebf6ee9bc','akshare_em_unadjusted_20260316_20261106','baostock','local_tushare_proxy_cache','local_akshare_event_cache'],
        warning='Diagnostic cycles only; no certified full-market, annual seasonality or point-in-time prediction claim',
        industry_theme_status='unavailable_no_certified_historical_mapping')
    dump(out/'cycles.json',all_cycles);dump(out/'coverage.json',months);dump(out/'metadata.json',meta);dump(out/'source_evidence.json',proof)
    # Nested evidence remains JSON; compact scalar labels are directly queryable in DuckDB.
    scalar=[{k:v for k,v in c.items() if k not in ('progress','candles','feature_provenance','market','windows')} for c in all_cycles]
    pq.write_table(pa.Table.from_pylist(scalar),out/'cycles.parquet',compression='zstd')
    manifest={'dataset':str(out.relative_to(ROOT)),'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()},'label_version':VERSION}
    tmp=ROOT/'manifest.tmp';dump(tmp,manifest);tmp.replace(ROOT/'manifest.json')
    print(json.dumps(meta,default=str,ensure_ascii=False));print('completed height distribution',Counter(c['max_height'] for c in all_cycles if c['cycle_complete']))

if __name__=='__main__':build()
