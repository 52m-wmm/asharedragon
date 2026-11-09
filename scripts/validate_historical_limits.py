"""Read-only offline validation. Writes separate research artifacts, never price tables.
PYTHONPATH=backend .venv/bin/python scripts/validate_historical_limits.py
"""
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
import csv
import hashlib
import json
from pathlib import Path
import duckdb
from sqlalchemy import text
from app.db.session import engine
from app.research.limit_rules import FACTOR_POLICY, VERSION, reconstruct, reconcile_event, score, l1_labels

BASE=Path.home()/'Datasets/asharedragon'
PURCHASED=BASE/'processed/stocks/daily/purchased_usb_0dfebf6ee9bc'
OUT=BASE/'research/historical_limits_v1'

def write_json(name,data):
    (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str)+'\n')

def write_csv(name,rows,columns):
    with (OUT/name).open('w') as f:
        writer=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');writer.writeheader();writer.writerows(rows)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run(*, output=None, export_facts=False):
    global OUT
    if output is not None: OUT=Path(output)
    exported=[]
    OUT.mkdir(parents=True,exist_ok=True)
    files=sorted(PURCHASED.glob('year=*/month=*/*.parquet'))
    hashes={str(p):digest(p) for p in files}
    with engine.connect() as c,c.begin():
        c.execute(text('SET TRANSACTION READ ONLY'))
        def pg(sql):return [dict(r) for r in c.execute(text(sql)).mappings()]
        cal=[r['trade_date'] for r in pg("select trade_date from trading_days where exchange='SSE' order by trade_date")]
        stocks=pg("select distinct on(stock_code) stock_code,listing_date,delisting_date,raw_id from stocks where provider='baostock' order by stock_code,id desc")
        daily=pg("select distinct on(provider,stock_code,trade_date,adjustment) stock_code,trade_date,provider,raw_id,adjustment,open,high,low,close,pre_close,trade_status,is_st from stock_daily where adjustment='none' order by provider,stock_code,trade_date,adjustment,id desc")
        raw=pg("select id,provider,endpoint,trade_date,parameters,ingestion_status,sha256,raw_payload from raw_observations where endpoint in ('limit_list_d','stock_zt_pool_em','stock_zt_pool_zbgc_em','stock_zt_pool_dtgc_em','stk_limit') order by id")
        parts=pg("select * from proxy_partitions where endpoint='limit_list_d' and status='success' order by id")
        events=pg('select stock_code,trade_date,provider,endpoint,raw_id,limit_type,limit_price,consecutive_limit_days from limitup_events order by id')
        counts=pg("select 'stock_daily' name,count(*) rows,max(id) max_id from stock_daily union all select 'limitup_events',count(*),max(id) from limitup_events")
    catalog={r['stock_code']:r for r in stocks}
    idx={d:i for i,d in enumerate(cal)}
    db=duckdb.connect(config={'memory_limit':'1GB','threads':2})
    db.read_parquet([str(p) for p in files],hive_partitioning=False).create_view('daily')
    def query(sql):
        r=db.execute(sql);return [dict(zip([c[0] for c in r.description],v)) for v in r.fetchall()]
    # Entire factor column is unapproved. Separate anomaly rows never replace original factors.
    db.sql('''with x as(select stock_code,trade_date,adj_factor,pre_close,
        lag(adj_factor) over w previous_factor,lag(close) over w previous_close
        from daily window w as(partition by stock_code order by trade_date))
        select *,true as formal_factor_use_blocked,
        abs(pre_close-previous_close)<=0.011 as unchanged_reference,
        abs(pre_close*adj_factor/previous_factor-previous_close)>0.02 as bridge_residual
        from x where abs(adj_factor/previous_factor-1)>1e-8 and
        (abs(pre_close-previous_close)<=0.011 or abs(pre_close*adj_factor/previous_factor-previous_close)>0.02)
        ''').write_parquet(str(OUT/'factor_anomalies.parquet'),compression='zstd')
    factor_count=db.read_parquet(str(OUT/'factor_anomalies.parquet')).count('*').fetchall()[0][0]
    write_json('factor_policy.json',{**FACTOR_POLICY,'flagged_rows':factor_count,'scope':'all purchased adj_factor values unapproved; flagged rows are diagnostic subset'})
    by_raw={r['id']:r for r in raw}
    selected={}
    for p in parts:
        if set(p['parameters'])=={'trade_date'} and not p['error_message'] and p['raw_id'] in by_raw:
            key=('tushare_proxy','limit_list_d',p['trade_date'])
            if key in selected and selected[key]['source_id']!=p['source_id']:
                raise ValueError('Multiple Tushare sources require explicit reconciliation')
            selected[key]=p
    for r in raw:
        if r['provider']=='akshare' and r['ingestion_status']=='success' and r['raw_payload'].get('records'):
            selected[(r['provider'],r['endpoint'],r['trade_date'])]={'raw_id':r['id']}
    selected_ids={p['raw_id'] for p in selected.values()}
    chosen=[e for e in events if e['raw_id'] in selected_ids]
    prices=[e for e in chosen if e['limit_price'] is not None]
    raw_stk=[r for r in raw if r['endpoint']=='stk_limit' and r['ingestion_status']=='success' and r['raw_payload'].get('records')]
    # No hidden promotion of an event close/high into an independent historical price.
    if raw_stk: raise ValueError('New stk_limit cache found: normalize/reconcile its independent prices before rerunning')
    source_closes={}
    source_names={}
    for r in raw:
        if r['id'] not in selected_ids:continue
        for v in r['raw_payload'].get('records',[]):
            if r['provider']=='tushare_proxy' and v.get('ts_code') and v.get('close') is not None:
                source_closes[(v['ts_code'],r['trade_date'])]=Decimal(str(v['close']))
                source_names[(v['ts_code'],r['trade_date'])]=v.get('name','')
    local={(r['stock_code'],r['trade_date']):r for r in daily if r['provider']=='baostock'}
    samples=[('2020-12',date(2020,12,1),date(2020,12,31),'tushare_proxy',date(2020,11,2)),
             ('2026-recent',date(2026,10,26),date(2026,11,4),'akshare',date(2026,10,19))]
    results=[];all_unknown=[];price_comparisons=[];all_height=[];coverage=[];formal=[];conflicts=[];assessments=[]
    for name,start,end,provider,warmup in samples:
        dates=[d for d in cal if start<=d<=end]
        full_cal=[d for d in cal if warmup<=d<=end]
        sample_events=[e for e in chosen if e['provider']==provider and warmup<=e['trade_date']<=end]
        grouped=defaultdict(list)
        for e in sample_events:grouped[(e['stock_code'],e['trade_date'])].append(e)
        event_map={k:v[0] for k,v in grouped.items() if len({e['limit_type'] for e in v})==1}
        event_conflicts={k for k,v in grouped.items() if len({e['limit_type'] for e in v})>1}
        # Benchmark contains explicitly observed U, Z, D. No absent-pool negatives.
        truth={k:e['limit_type']=='up' for k,e in event_map.items() if start<=k[1]<=end}
        if provider=='tushare_proxy':
            rows=query(f"select * from daily where trade_date between '{warmup}' and '{end}'")
        else:
            rows=[dict(r,adjustment_type='none') for r in local.values() if warmup<=r['trade_date']<=end]
        row_map={(r['stock_code'],r['trade_date']):r for r in rows}
        strict={};diagnostic={};reasons=Counter();assumptions=Counter()
        target_keys={k for k in row_map if start<=k[1]<=end}|set(truth)|event_conflicts
        for d in dates:
            target_keys.update((code,d) for code,s in catalog.items() if s['listing_date'] and s['listing_date']<=d and (not s['delisting_date'] or s['delisting_date']>=d))
        keys=target_keys|set(row_map)
        for key in sorted(keys):
            code,day=key;r=row_map.get(key);s=catalog.get(code,{})
            state=local.get(key,{})
            listing=s.get('listing_date');n=idx[day]-idx[listing]+1 if listing in idx else None
            conflict=key in event_conflicts
            if r and state and any(r.get(k) is not None and state.get(k) is not None and abs(Decimal(str(r[k]))-state[k])>Decimal('.001') for k in ('open','high','low','close','pre_close')):
                conflict=True
            ev=dict(listing_date=listing,delisting_date=s.get('delisting_date'),listing_session=n,
                    trade_status=state.get('trade_status'),is_st=state.get('is_st'),special_status='unknown',
                    reference_verified=bool(state.get('pre_close') is not None),conflict=conflict)
            if code=='600182.SH' and date(2020,1,1)<=day<=date(2020,12,31):
                ev['special_status']='legacy_non_reform'  # 2020 company report + SSE 2014 S-share rule
            if code=='000995.SZ' and day==date(2020,12,16):
                ev.update(special_status='no_limit',trade_status=True)  # exchange-hosted resumption notice
            previous_day=cal[idx[day]-1] if idx[day]>0 else None
            if local.get((code,previous_day),{}).get('trade_status') is False:
                ev['special_status']='resumption_unverified'
            fact=reconstruct(r,ev)
            strict[key]=dict(stock_code=code,trade_date=day,**fact)
            de=dict(ev)
            # Tushare inclusion proves non-ST scope for THAT date only, not adjacent dates.
            if provider=='tushare_proxy' and key in event_map and de['is_st'] is None:
                de['is_st']=False
            candidate=reconstruct(r,de,diagnostic=True)
            if provider=='tushare_proxy' and key in event_map:
                candidate['assumptions'].append('non_st_scope_from_reference_endpoint_not_independent_status')
            diagnostic[key]=dict(stock_code=code,trade_date=day,**candidate)
            if key in target_keys:
                reasons[fact['reason']]+=1
                assumptions.update(candidate['assumptions'])
                if fact['classification']=='unknown':all_unknown.append(dict(sample=name,stock_code=code,trade_date=day,reason=fact['reason'],diagnostic=candidate['classification']))
            if conflict and key in target_keys:
                conflicts.append(dict(sample=name,stock_code=code,trade_date=day,reason='source_conflict'))
            if key in event_map:
                combined=reconcile_event(fact,event_map[key])
                diagnostic_conflict=reconcile_event(candidate,event_map[key])['status']=='unknown'
                close_conflict=bool(r and key in source_closes and abs(Decimal(str(r['close']))-source_closes[key])>Decimal('.001'))
                if combined['status']=='unknown' or close_conflict or diagnostic_conflict:
                    conflicts.append(dict(sample=name,stock_code=code,trade_date=day,event=event_map[key]['limit_type'],rule=fact['classification'],close_conflict=close_conflict,reason='diagnostic_event_conflict' if diagnostic_conflict else combined.get('reason','source_price_conflict')))
        if export_facts:
            conflict_keys={(x['stock_code'],x['trade_date']) for x in conflicts if x['sample']==name}
            for key,g in diagnostic.items():
                exported.append(dict(sample=name,provider=provider,stock_code=key[0],trade_date=key[1],
                    classification='unknown' if key in conflict_keys else g['classification'],
                    reason='source_or_rule_conflict' if key in conflict_keys else g['reason'],
                    assumptions=g['assumptions'],strict_eligible=strict[key]['formal_eligible'],
                    in_target=start<=key[1]<=end))
        for key,y in truth.items():
            f=strict[key];g=diagnostic[key];r=row_map.get(key,{})
            discrepant=g['classification']!='unknown' and (g['classification']=='up')!=y
            assessments.append(dict(sample=name,stock_code=key[0],trade_date=key[1],source_event=event_map[key]['limit_type'],source_name=source_names.get(key),strict=f['classification'],strict_reason=f['reason'],diagnostic=g['classification'],derived_limit=g['limit_price'],close=r.get('close'),pre_close=r.get('pre_close'),final_status='unknown_diagnostic_conflict' if discrepant else 'observed_event_not_formal_label',raw_id=event_map[key]['raw_id']))
        strict_metric=score(strict,truth);diag_metric=score(diagnostic,truth)
        errs=[]
        for key,y in truth.items():
            f=diagnostic[key]
            if f['classification'] in ('up','not_up','suspended','no_limit') and (f['classification']=='up')!=y:
                r=row_map.get(key,{})
                errs.append(dict(stock_code=key[0],trade_date=key[1],truth=y,classification=f['classification'],reason=f['reason'],limit_price=f['limit_price'],close=r.get('close'),pre_close=r.get('pre_close')))
        for e in sample_events:
            key=e['stock_code'],e['trade_date']
            if start<=key[1]<=end and e['limit_price'] is not None:
                f=diagnostic.get(key,{})
                price_comparisons.append(dict(stock_code=key[0],trade_date=key[1],provider=provider,event_limit_price=e['limit_price'],derived_limit=f.get('limit_price'),difference=(f['limit_price']-e['limit_price']) if f.get('limit_price') else None,quality='event_quote_crosscheck_not_independent_official_price'))
        # Reconstruct diagnostic streaks over exact calendar; no use of vendor height as seed.
        codes={k[0] for k in truth}
        for code in sorted(codes):
            height=None;cycle=None
            for day in full_cal:
                f=diagnostic.get((code,day),{'classification':'unknown'});kind=f['classification']
                if kind=='up':
                    if height==0:cycle=day
                    if height is not None:height+=1
                elif kind in ('not_up','suspended','no_limit'):height,cycle=0,None
                else:height,cycle=None,None
                e=event_map.get((code,day))
                if start<=day<=end and e and e['limit_type']=='up':
                    all_height.append(dict(sample=name,stock_code=code,trade_date=day,source_height=e['consecutive_limit_days'],diagnostic_height=height if kind=='up' else None,diagnostic_cycle_start=cycle if kind=='up' else None,formal=False))
            facts=[strict[(code,d)] for d in full_cal if (code,d) in strict]
            # Entire interval currently fails special-session evidence and independent price verification.
            formal.extend(l1_labels(facts,full_cal,coverage_complete=all((code,d) in strict and strict[(code,d)]['formal_eligible'] for d in full_cal),rules_validated=False))
        for day in dates:
            ks=[k for k in target_keys if k[1]==day]
            coverage.append(dict(sample=name,trade_date=day,scope_rows=len(ks),daily_rows=sum(k in row_map for k in ks),strict_classified=sum(strict[k]['classification']!='unknown' for k in ks),unknown=sum(strict[k]['classification']=='unknown' for k in ks),independent_limit_prices=0,formal_interval_passed=False))
        result=dict(sample=name,start=start,end=end,sessions=len(dates),reference_conflicts_excluded=sum(start<=k[1]<=end for k in event_conflicts),reference_dates=sorted({k[1] for k in truth}),benchmark_scope='explicit U/Z/D event records only; no absence-as-negative',strict=strict_metric,diagnostic=diag_metric,scope_stock_days=len(target_keys),strict_unknown=sum(strict[k]['classification']=='unknown' for k in target_keys),unknown_reasons=dict(reasons),diagnostic_assumptions=dict(assumptions),diagnostic_errors=errs)
        results.append(result)
    # All purchased dates lack an independently stored stk_limit snapshot.
    purchased_dates=query('select trade_date,count(*) rows from daily group by 1 order by 1')
    write_csv('missing_historical_limit_dates.csv',[dict(**r,independent_prices=0,reason='no_independent_historical_limit_price_cache') for r in purchased_dates],['trade_date','rows','independent_prices','reason'])
    write_csv('unknown_stock_days.csv',all_unknown,['sample','stock_code','trade_date','reason','diagnostic'])
    unknown_codes=Counter(r['stock_code'] for r in all_unknown)
    write_csv('unknown_stocks.csv',[dict(stock_code=k,unknown_stock_days=v) for k,v in sorted(unknown_codes.items())],['stock_code','unknown_stock_days'])
    write_csv('sample_coverage.csv',coverage,['sample','trade_date','scope_rows','daily_rows','strict_classified','unknown','independent_limit_prices','formal_interval_passed'])
    write_csv('height_comparison.csv',all_height,['sample','stock_code','trade_date','source_height','diagnostic_height','diagnostic_cycle_start','formal'])
    write_csv('price_comparison.csv',price_comparisons,['stock_code','trade_date','provider','event_limit_price','derived_limit','difference','quality'])
    write_csv('event_assessments.csv',assessments,['sample','stock_code','trade_date','source_event','source_name','strict','strict_reason','diagnostic','derived_limit','close','pre_close','final_status','raw_id'])
    write_json('formal_l1_labels.json',formal)
    write_json('source_conflicts.json',conflicts)
    for result in results:
        heights=[h for h in all_height if h['sample']==result['sample']]
        comparable=[h for h in heights if h['source_height'] is not None and h['diagnostic_height'] is not None]
        result['height_validation']=dict(observed=len(heights),comparable=len(comparable),matched=sum(h['source_height']==h['diagnostic_height'] for h in comparable),mismatched=sum(h['source_height']!=h['diagnostic_height'] for h in comparable),unknown=len(heights)-len(comparable),formal=False)
    summary=dict(version=VERSION,samples=results,formal_label_count=len(formal),formal_years=[],
                 independent_price_rows=0,event_price_rows=len(prices),sample_event_price_rows=len(price_comparisons),sample_event_price_comparable=sum(p['difference'] is not None for p in price_comparisons),sample_event_price_mismatches=sum(p['difference'] is not None and p['difference']!=0 for p in price_comparisons),missing_price_dates=len(purchased_dates),
                 factor_flagged_rows=factor_count,unknown_stock_count=len(unknown_codes),conflicts=len(conflicts),
                 rule_gate='blocked_missing_special_history_and_independent_validation',protected_hashes_unchanged=all(digest(Path(p))==h for p,h in hashes.items()),
                 database_read_only=True,source_snapshot_ids=sorted(selected_ids),source_snapshot_hashes={str(r['id']):r['sha256'] for r in raw if r['id'] in selected_ids},protected_partition_hashes=hashes,database_counts=counts)
    write_json('validation.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k not in ('source_snapshot_ids','source_snapshot_hashes','protected_partition_hashes')},ensure_ascii=False,indent=2,default=str))
    if export_facts:
        import pyarrow as pa
        import pyarrow.parquet as pq
        pq.write_table(pa.Table.from_pylist(exported),OUT/'diagnostic_stock_days.parquet',compression='zstd')
    db.close()
if __name__=='__main__':run()
