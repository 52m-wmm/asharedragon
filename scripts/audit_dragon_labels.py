"""v0.5.1 read-only funnel and case audit; price scenarios NEVER enter labels."""
from collections import Counter,defaultdict
from datetime import date,datetime,timezone,timedelta
from pathlib import Path
import csv,hashlib,json
import pyarrow.parquet as pq
import pyarrow as pa
from app.research.queries import Research
from app.research.dragon_queries import Dragons
from app.research.limit_rules import reconstruct
from app.research.dragons import research_state
from build_us_market_signals import event_snapshot

BASE=Path.home()/'Datasets/asharedragon'
ROOT=BASE/'research/dragon_diagnostics_v051'
INPUT=BASE/'research/dragon_v1/input_validation'
CASES=[('002432.SZ','九安医疗',2021),('000957.SZ','中通客车',2022),('603178.SH','圣龙股份',2023),('603960.SH','克来机电',2024),('603580.SH','艾艾精工',2024)]


def save(name,value):
    (ROOT/name).write_text(json.dumps(value,ensure_ascii=False,default=str,indent=2,allow_nan=False))


def price_runs(records):
    """OHLC sensitivity runs only. No synthetic event objects or formal tags."""
    runs=[];active=None;previous=None
    for row in records:
        kind=row['scenario_classification']
        if kind=='up':
            if active is None:active={'start':row['trade_date'],'left_boundary_known_under_assumptions':previous in ('not_up','no_limit','suspended'),'dates':[]}
            active['dates'].append(row['trade_date'])
        elif active:
            active.update(end=active['dates'][-1],observed_run_length=len(active['dates']),right_boundary=row['trade_date'] if kind in ('not_up','no_limit','suspended') else None,
                research_state='diagnostic',formal_eligible=False,scenario_only=True)
            runs.append(active);active=None
        previous=kind
    if active:
        active.update(end=active['dates'][-1],observed_run_length=len(active['dates']),right_boundary=None,research_state='diagnostic',formal_eligible=False,scenario_only=True);runs.append(active)
    return runs


def audit():
    ROOT.mkdir(parents=True,exist_ok=True)
    notices=json.loads((ROOT/'sources/manifest.json').read_text())
    for key in ('zhongtong_resume','zhongtong_july'):
        assert notices[key].get('sha256')==hashlib.sha256((ROOT/'sources'/(key+'.pdf')).read_bytes()).hexdigest(), 'Suspension evidence must be verified first'
    service=Dragons();snapshot=json.loads((BASE/'research/dragon_v1/manifest.json').read_text())
    if not (ROOT/'baseline.json').exists():save('baseline.json',{'manifest':snapshot,'meta':service.meta,'quality_counts':dict(Counter(c['quality'] for c in service.rows))})
    facts=pq.read_table(INPUT/'diagnostic_stock_days.parquet').to_pylist();gate=json.loads((INPUT/'validation.json').read_text())
    calendar,pools,evidence,issues=event_snapshot();idx={d:i for i,d in enumerate(calendar)}
    target={(r['provider'],r['stock_code'],r['trade_date']):r for r in facts if r['in_target']}
    all_facts={(r['sample'],r['stock_code'],r['trade_date']):r for r in facts}
    sample_dates={date.fromisoformat(s['start'])+timedelta(days=i) for s in gate['samples'] for i in range((date.fromisoformat(s['end'])-date.fromisoformat(s['start'])).days+1)}
    up=[]
    for (provider,d,kind),pool in pools.items():
        if kind=='U':up.extend({'provider':provider,'trade_date':d,'raw_id':pool['raw_id'],**r} for r in pool['rows'])
    scoped=[r for r in up if (r['provider'],r['stock_code'],r['trade_date']) in target]
    bad={}
    for provider,d,k in pools:
        u=pools.get((provider,d,'U'));z=pools.get((provider,d,'Z'))
        codes=Counter(x['stock_code'] for x in (u or {}).get('rows',[])+(z or {}).get('rows',[]))
        bad[provider,d]={code for code,n in codes.items() if n>1}
    clean=[x for x in scoped if x['stock_code'] not in bad[x['provider'],x['trade_date']]]
    clean_keys={(x['provider'],x['stock_code'],x['trade_date']) for x in clean}
    excluded=Counter();confirmed=[]
    for r in clean:
        f=target[r['provider'],r['stock_code'],r['trade_date']]
        if f['classification']=='up':confirmed.append(r)
        else:excluded[f['reason']]+=1
    proof_counts=Counter();trace=[]
    for c in service.rows:
        first=date.fromisoformat(c['first_observed_date']);last=date.fromisoformat(c['progress'][-1]['trade_date'])
        prev=calendar[idx[first]-1] if idx[first]>0 else None;next_day=calendar[idx[last]+1] if idx[last]+1<len(calendar) else None
        left=all_facts.get((c['sample'],c['stock_code'],prev),{});right=all_facts.get((c['sample'],c['stock_code'],next_day),{})
        end=date.fromisoformat(next(s['end'] for s in gate['samples'] if s['sample']==c['sample']))
        def failure_reason(f,day):
            if not f:return 'missing_prior_or_next_audit_fact'
            if c['stock_code'] in bad.get((c['provider'],day),set()):return 'duplicate_or_conflicting_event'
            if f['classification']=='up':
                if f['in_target'] and (c['provider'],c['stock_code'],day) not in clean_keys:return 'no_corresponding_up_event'
                return 'prior_up_left_censored'
            return f['reason']
        left_reason=failure_reason(left,prev) if not c['left_boundary_verified'] else 'established'
        if c['quality']=='unverified_event_observation':left_reason=all_facts.get((c['sample'],c['stock_code'],first),{}).get('reason','missing_fact')
        right_reason='established' if c['right_boundary_date'] else 'audit_interval_end' if next_day is None or next_day>end else failure_reason(right,next_day)
        if not c['cycle_complete']:proof_counts[left_reason if not c['left_boundary_verified'] else right_reason]+=1
        trace.append({'cycle_id':c['cycle_id'],'stock_code':c['stock_code'],'sample':c['sample'],'first_observed_date':first,
            'first_board_date':c['first_board_date'],'quality':c['quality'],'research_state':research_state(c),
            'left_boundary_date':prev,'left_reason':left_reason,'right_boundary_date':next_day,'right_reason':right_reason,
            'max_height':c['max_height'],'observed_run_length':len(c['progress'])})
    with (ROOT/'cycle_exclusions.csv').open('w') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(trace[0]));writer.writeheader();writer.writerows(trace)
    cases=[];case_rows=[]
    with Research() as r:
        sql="""SELECT count(*) AS rows,count(DISTINCT(stock_code,trade_date)) AS unique_stock_days,
        count(*) FILTER(WHERE isfinite(open) AND isfinite(high) AND isfinite(low) AND isfinite(close) AND low>0 AND high>=greatest(open,close) AND low<=least(open,close) AND high>=low) AS valid_ohlc_rows,
        count(is_st) AS known_st_rows,count(trade_status) AS known_trade_status_rows FROM daily"""
        daily=r.query(sql)[0]
        fullcal=[v['trade_date'] for v in r.query('SELECT trade_date FROM calendar ORDER BY trade_date')];full_idx={d:i for i,d in enumerate(fullcal)}
        daily['target_interval_price_rows']=r.query('SELECT count(*) n FROM daily WHERE trade_date=ANY(?)',[sorted(sample_dates)])[0]['n']
        daily['observed_sessions']=r.query('SELECT count(DISTINCT trade_date) n FROM daily')[0]['n']
        daily['recent_gap_count']=r.query("SELECT count(*) n FROM read_parquet(?) WHERE trade_date>='2026-03-16'",[str(BASE/'research/missing_stock_dates.parquet')])[0]['n']
        for code,name,year in CASES:
            lo=date(year-1,12,1);hi=date(year+1,1,31);days=[d for d in fullcal if lo<=d<=hi]
            prices=r.query('SELECT * FROM daily WHERE stock_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date',[code,lo,hi]);byday={x['trade_date']:x for x in prices}
            stock=r.query('SELECT * FROM stocks WHERE stock_code=?',[code])[0];listed=stock['listing_date'];records=[]
            for d in days:
                row=byday.get(d);ev={'listing_date':listed,'delisting_date':stock['delisting_date'],'listing_session':full_idx[d]-full_idx[listed]+1,
                    'is_st':row.get('is_st') if row else None,'trade_status':row.get('trade_status') if row else None,
                    'special_status':'unknown','reference_verified':False}
                suspension=None
                if code=='000957.SZ':
                    for start,end,key in [('2022-06-02','2022-06-09','zhongtong_resume'),('2022-06-22','2022-07-05','zhongtong_july')]:
                        if date.fromisoformat(start)<=d<=date.fromisoformat(end):suspension=key
                    if suspension:ev.update(trade_status=False,special_status='suspended',status_source=suspension)
                    if d in (date(2022,6,10),date(2022,7,6)):ev['special_status']='resumption_unverified'
                strict=reconstruct(row,ev)
                diagnostic=reconstruct(row,ev,diagnostic=True)
                # Hypothesis only. Never used as actual historical ST evidence.
                scenario=reconstruct(row,{**ev,'is_st':False},diagnostic=True)
                records.append({'stock_code':code,'trade_date':d,'in_case_year':d.year==year,'daily_present':row is not None,
                    'source':row.get('source') if row else None,'close':row.get('close') if row else None,'pre_close':row.get('pre_close') if row else None,
                    'strict_classification':strict['classification'],'strict_reason':strict['reason'],
                    'diagnostic_classification':diagnostic['classification'],'diagnostic_reason':diagnostic['reason'],
                    'scenario_classification':scenario['classification'],'scenario_limit':float(scenario['limit_price']) if scenario['limit_price'] else None,
                    'scenario_assumptions':scenario['assumptions']+['non_st_assumed_for_price_sensitivity_only'],
                    'suspension_notice_key':suspension,
                    'suspension_source_url':notices[suspension]['url'] if suspension else None,
                    'suspension_source_sha256':notices[suspension]['sha256'] if suspension else None,
                    'suspension_source_observed_at_utc':datetime.fromtimestamp((ROOT/'sources'/(suspension+'.pdf')).stat().st_mtime,timezone.utc) if suspension else None,
                    'cached_up_event':False})
            # All local event evidence is checked, not only the two target intervals.
            cached_dates={x['trade_date'] for x in up if x['stock_code']==code}
            for row in records:row['cached_up_event']=row['trade_date'] in cached_dates
            selected=[v for v in records if v['in_case_year']]
            runs=[x for x in price_runs(records) if any(d.year==year for d in x['dates'])]
            runs.sort(key=lambda x:x['observed_run_length'],reverse=True)
            cases.append({'stock_code':code,'name':name,'year':year,'expected_sessions':len(selected),'observed_rows':sum(x['daily_present'] for x in selected),
                'missing_dates':[x['trade_date'] for x in selected if not x['daily_present']],
                'notice_explained_suspensions':[x['trade_date'] for x in selected if x['suspension_notice_key']],
                'known_st_days':sum(byday.get(x['trade_date'],{}).get('is_st') is not None for x in selected),
                'strict_counts':dict(Counter(x['strict_classification'] for x in selected)),
                'strict_reasons':dict(Counter(x['strict_reason'] for x in selected)),
                'diagnostic_counts_without_assumed_st':dict(Counter(x['diagnostic_classification'] for x in selected)),
                'cached_up_event_days':sum(x['cached_up_event'] for x in selected),'official_limit_rows':0,
                'production_audit_days':sum((provider,code,x['trade_date']) in target for x in selected for provider in ('akshare','tushare_proxy')),
                'production_cycles':sum(c['stock_code']==code and (c['first_board_date'] or c['first_observed_date']).startswith(str(year)) for c in service.rows),
                'price_scenario_runs_ge3':[x for x in runs if x['observed_run_length']>=3],
                'longest_price_scenario':runs[0] if runs else None,'research_state':'unknown','formal_eligible':False,
                'missing_evidence':['historical ST state','complete special-session state','independent daily limits or certified ex-reference','independent complete daily events','approved research interval'],
                'source':'purchased_usb_0dfebf6ee9bc; official notices are separate corroboration, not event labels'})
            case_rows.extend(records)
    pq.write_table(pa.Table.from_pylist(case_rows),ROOT/'case_stock_days.parquet',compression='zstd')
    save('cases.json',cases)
    stats={
        'daily':daily,'audit_sessions':len({r['trade_date'] for r in target.values()}),'audit_stock_day_denominator':len(target),
        'all_cached_up_rows':len(up),'all_cached_up_stock_days':len({(x['provider'],x['stock_code'],x['trade_date']) for x in up}),
        'scope_up_rows':len(scoped),'scope_unique_up_stock_days':len({(x['provider'],x['stock_code'],x['trade_date']) for x in scoped}),
        'duplicate_or_conflicting_up_rows_removed':len(scoped)-len(clean),'clean_scope_up_rows':len(clean),'outside_scope_up_rows':len(up)-len(scoped),
        'source_first_board_rows':sum(x['consecutive_limit_days']==1 for x in scoped),
        'clean_source_first_board_rows':sum(x['consecutive_limit_days']==1 for x in clean),
        'price_supported_source_first_board_rows':sum(x['consecutive_limit_days']==1 for x in confirmed),
        'diagnostic_confirmed_up_rows':len(confirmed),'excluded_up_rows_by_reason':dict(excluded),
        'strict_confirmed_up_rows':sum(s['strict']['tp'] for s in gate['samples']),
        'candidate_records':len(service.rows),'event_fragments':sum(c['quality']!='unverified_event_observation' for c in service.rows),
        'unverified_single_event_records':sum(c['quality']=='unverified_event_observation' for c in service.rows),
        'known_first_boards':sum(c['first_board_date'] is not None for c in service.rows),
        'unknown_left_boundary':sum(not c['left_boundary_verified'] for c in service.rows),
        'known_start_unknown_end':sum(c['left_boundary_verified'] and not c['cycle_complete'] for c in service.rows),
        'complete_cycles':sum(c['cycle_complete'] for c in service.rows),
        'quality_counts':dict(Counter(c['quality'] for c in service.rows)),
        'exclusive_exclusion_causes':dict(proof_counts),
        'thresholds':{str(n):{'known_reached':sum(c[f'reached_{n}'] is True for c in service.rows),
                            'complete':sum(c['max_height'] is not None and c['max_height']>=n for c in service.rows),
                            'unknown':sum(c[f'reached_{n}'] is None for c in service.rows)} for n in (3,5,7,10)},
        'fact_unknown_reasons':dict(Counter(f['reason'] for f in target.values() if f['classification']=='unknown')),
        'strict_reasons_by_sample':{s['sample']:s['unknown_reasons'] for s in gate['samples']},
        'research_state_counts':{state:sum(research_state(c)==state for c in service.rows) for state in ('verified','diagnostic','unknown')},
        'source_snapshot':snapshot,'cases':cases,'created_at_utc':datetime.now(timezone.utc),
        'formal_count':0,'cases_are_not_hardcoded_labels':True,'hypothetical_price_runs_excluded_from_production':True}
    assert stats['clean_scope_up_rows']==stats['diagnostic_confirmed_up_rows']+sum(excluded.values())
    assert stats['candidate_records']==stats['event_fragments']+stats['unverified_single_event_records']
    assert stats['candidate_records']==stats['known_first_boards']+stats['unknown_left_boundary']
    assert stats['known_first_boards']==stats['complete_cycles']+stats['known_start_unknown_end']
    assert sum(proof_counts.values())==stats['candidate_records']-stats['complete_cycles']
    save('audit.json',stats)
    if not (ROOT/'audit_baseline.json').exists():save('audit_baseline.json',stats)
    print(json.dumps({k:v for k,v in stats.items() if k not in ('cases','source_snapshot','strict_reasons_by_sample')},ensure_ascii=False,default=str,indent=2))
    for c in cases:print(c['name'],c['observed_rows'],c['expected_sessions'],c['missing_dates'],c['longest_price_scenario'])

if __name__=='__main__':audit()
