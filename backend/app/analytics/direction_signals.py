"""Versioned descriptive directions. Comparability precedes arithmetic."""
from collections import Counter
from decimal import Decimal
import math

RULE_VERSION = 'direction-signals-v1'
POLICY = dict(window='previous_SSE_session',neutral_delta=0,
              max_unexplained_sample_differences=0,max_critical_price_conflicts=0,
              coverage='observed_sample_only; whole_market_coverage_remains_null',
              cutoff='frozen_EOD_release_not_point_in_time')
BREADTH_RULES = (
    ('up','上涨家数','count',1),('down','下跌家数','count',-1),
    ('advancing_ratio','上涨占比','ratio',1),('median_return','收益中位数','ratio',1),
)
LIMIT_RULES = (
    ('limit_up_count','涨停热度','count',1),('broken_limit_rate','炸板率','ratio',-1),
    ('promotion_1to2','一进二晋级率','ratio',1),('promotion_2to3','二进三晋级率','ratio',1),
)


def finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def breadth_comparison(previous, current):
    """Profiles contain full observed/valid sets, not only equal aggregate counts."""
    reasons=[]
    if previous is None or current is None:
        return dict(reasons=['comparison_profile_missing'],overlap=None)
    a,b=previous['valid'],current['valid'];pa,pb=set(a),set(b)
    oa,ob=set(previous['observed']),set(current['observed'])
    added=sorted(pb-pa);removed=sorted(pa-pb)
    changed=sorted(c for c in pa&pb if a[c]!=b[c])
    quality_changes=sorted(c for c in oa&ob if previous['observed'][c]!=current['observed'][c])
    if oa!=ob: reasons.append('observed_universe_changed_without_verified_explanation')
    if added or removed: reasons.append('valid_sample_changed_without_verified_explanation')
    if changed: reasons.append('price_source_or_reference_basis_changed')
    if quality_changes: reasons.append('per_stock_price_quality_changed')
    if previous['critical_price_conflicts'] or current['critical_price_conflicts']:
        reasons.append('critical_price_conflict')
    return dict(reasons=reasons,overlap=dict(previous_count=len(pa),current_count=len(pb),
        common_count=len(pa&pb),previous_overlap=len(pa&pb)/len(pa) if pa else None,
        current_overlap=len(pa&pb)/len(pb) if pb else None,
        jaccard=len(pa&pb)/len(pa|pb) if pa|pb else None),
        added_codes=added,removed_codes=removed,source_changed_codes=changed,
        observed_added_codes=sorted(ob-oa),observed_removed_codes=sorted(oa-ob),quality_changed_codes=quality_changes)


def breadth_quality(row):
    if not row: return ['breadth_day_missing']
    try:
        n=row['price_valid_count'];o=row['observed_count']
        if any(not isinstance(row[k],int) or isinstance(row[k],bool) or row[k]<0 for k in
            ('price_valid_count','observed_count','price_unknown_count','up','down','flat',
             'non_st_confirmed_count','st_confirmed_count','st_unknown_count',
             'valid_non_st_count','valid_st_count','valid_st_unknown_count')):
            return ['breadth_invalid_counts']
        if row['status']!='observed_sample' or n<=0: return ['no_valid_breadth_sample']
        if (sum(row[k] for k in ('up','down','flat'))!=n or n+row['price_unknown_count']!=o
            or sum(row[k] for k in ('non_st_confirmed_count','st_confirmed_count','st_unknown_count'))!=o
            or sum(row[k] for k in ('valid_non_st_count','valid_st_count','valid_st_unknown_count'))!=n):
            return ['breadth_count_conservation_failed']
        if not finite(row['advancing_ratio']) or abs(row['advancing_ratio']-row['up']/n)>1e-12:
            return ['breadth_ratio_inconsistent']
    except (KeyError,TypeError): return ['breadth_quality_fields_missing']
    return []


def limit_value(row, metric, diagnostic):
    if not row: return None,dict(numerator=None,denominator=None)
    if metric.startswith('promotion_'):
        q=row.get(metric,{})
        if diagnostic: q=q.get('supplier_diagnostic',{})
        return q.get('rate'),dict(numerator=q.get('promoted_count'),denominator=q.get('fixed_denominator'))
    q=row.get('supplier_diagnostic' if diagnostic else 'strict_non_st',{})
    name='limit_up_count' if diagnostic else 'up_count'
    if metric=='limit_up_count': return q.get(name),dict(numerator=q.get(name),denominator=None)
    return q.get('broken_limit_rate'),dict(numerator=q.get('break_numerator'),denominator=q.get('break_denominator'))


def limit_quality(row, metric, diagnostic):
    if not row: return ['limit_day_missing']
    reasons=[]
    required=('up','broken') if metric=='broken_limit_rate' else ('up',)
    for kind in required:
        p=row.get('pools',{}).get(kind,{})
        if p.get('status')!='supplier_observed': reasons.append(f'{kind}_pool_unavailable')
        if (not p.get('provider') or p.get('provider')!=row.get('provider')
            or str(p.get('trade_date'))!=str(row.get('trade_date')) or not p.get('scope')):
            reasons.append('pool_provenance_invalid')
        if not diagnostic and not p.get('scope_verified'): reasons.append('strict_source_scope_unverified')
    if metric=='broken_limit_rate' and row.get('overlap_codes'): reasons.append('up_broken_overlap')
    if metric=='broken_limit_rate':
        pools=row.get('pools',{})
        if pools.get('up',{}).get('scope')!=pools.get('broken',{}).get('scope'):
            reasons.append('up_broken_scope_incompatible')
    if metric.startswith('promotion_'):
        q=row.get(metric,{})
        if diagnostic:
            if q.get('supplier_diagnostic',{}).get('status')!='diagnostic': reasons.append('supplier_queue_unavailable')
        else:
            if q.get('status')!='confirmed' or q.get('cohort_status')!='confirmed': reasons.append('strict_queue_unconfirmed')
            if q.get('unresolved_count')!=0: reasons.append('strict_queue_unresolved')
            reasons.extend(q.get('cohort_reasons',[]))
    elif not diagnostic:
        for kind in required: reasons.extend(row.get('reasons',{}).get(kind,[]))
    value,parts=limit_value(row,metric,diagnostic)
    if metric=='limit_up_count' and (not finite(value) or value<0 or int(value)!=value):
        reasons.append('count_missing_or_invalid')
    if metric!='limit_up_count':
        n,d=parts['numerator'],parts['denominator']
        if not finite(d) or d<=0: reasons.append('missing_or_zero_denominator')
        elif not finite(n) or not 0<=n<=d or not finite(value) or abs(value-n/d)>1e-12:
            reasons.append('ratio_or_queue_count_inconsistent')
    return reasons


def limit_comparison(previous,current,metric):
    if not previous or not current: return ['limit_day_missing']
    reasons=[]
    for field in ('provider','rule_version','snapshot_id','source_snapshot'):
        if not previous.get(field) or not current.get(field): reasons.append(f'limit_{field}_missing')
        if previous.get(field)!=current.get(field): reasons.append(f'limit_{field}_changed')
    for kind in (('up','broken') if metric=='broken_limit_rate' else ('up',)):
        a=previous.get('pools',{}).get(kind,{});b=current.get('pools',{}).get(kind,{})
        if a.get('scope')!=b.get('scope') or a.get('provider')!=b.get('provider'):
            reasons.append('supplier_scope_or_provider_changed')
    return reasons


def signal(metric,name,unit,polarity,layer,value,previous_value,reasons,*,day,previous_day,
           source,snapshot_id,input_snapshots,basis):
    reasons=sorted(set(reasons))
    if not finite(value) or not finite(previous_value): reasons.append('value_missing_or_nonfinite')
    delta=None;direction='unknown'
    if not reasons:
        change=(Decimal(str(value))-Decimal(str(previous_value)))*(100 if unit=='ratio' else 1)
        delta=float(change)
        direction='neutral' if change==0 else 'positive' if change*polarity>0 else 'negative'
    diagnostic=layer=='supplier_diagnostic'
    prefix='供应商诊断；不代表严格非 ST 结论。' if diagnostic else '可观察样本广度；非全市场或严格非 ST。' if layer=='observed_breadth' else '严格非 ST。'
    reason=(prefix+'暂不可比：'+', '.join(reasons)) if reasons else (
        prefix+f'{previous_day} 的 {previous_value} → {day} 的 {value}；变化 {delta} '+('pp' if unit=='ratio' else '家')+
        f'；规则：增加表示{"改善" if polarity==1 else "风险增大"}，不变为中性。')
    return dict(id=layer+'.'+metric,name=name,layer=layer,trade_date=day,previous_trading_day=previous_day,
        direction=direction,value=value if finite(value) else None,previous_value=previous_value if finite(previous_value) else None,
        delta=delta,unit='ratio' if unit=='ratio' else 'count',delta_unit='pp' if unit=='ratio' else 'count',
        reason=reason,reason_codes=sorted(set(reasons)),status='unknown' if reasons else 'diagnostic' if diagnostic else 'formal',
        comparison_status='unavailable' if reasons else 'comparable',source=source,snapshot_id=snapshot_id,
        input_snapshots=input_snapshots,rule_version=RULE_VERSION,basis=basis,
        eligible_for_formal_counts=not diagnostic and not reasons)


def daily_signals(day, previous_day, breadth, previous_breadth, limits, previous_limits,
                  comparison, *, snapshot_id,input_snapshots,source_profiles=None):
    """Caller selects exact calendar predecessor; dates are rechecked here."""
    common=[]
    if not previous_day: common.append('previous_session_unavailable')
    signals=[]
    dates=[]
    for row,expected in ((breadth,day),(previous_breadth,previous_day)):
        if row is not None and str(row.get('trade_date'))!=str(expected): dates.append('breadth_date_mismatch')
    b_reasons=common+dates+breadth_quality(breadth)+breadth_quality(previous_breadth)+comparison['reasons']
    for metric,name,unit,polarity in BREADTH_RULES:
        def parts(row):
            if not row: return None
            return {k:row.get(k) for k in ('observed_count','price_valid_count','price_unknown_count','up',
                'st_unknown_count','st_confirmed_count','non_st_confirmed_count','coverage_ratio','universe_status',
                'lifecycle_conflict_observed_count')}
        basis=dict(formula='median(close/verified_reference-1)' if metric=='median_return' else 'up/price_valid_count' if metric=='advancing_ratio' else f'count({metric})',
                   previous=parts(previous_breadth),current=parts(breadth),comparison=comparison)
        signals.append(signal(metric,name,unit,polarity,'observed_breadth',(breadth or {}).get(metric),
            (previous_breadth or {}).get(metric),b_reasons,day=day,previous_day=previous_day,
            source=dict(kind='E1_observed_price_sample',includes_ST_and_unknown=True,profiles=source_profiles),
            snapshot_id=snapshot_id,input_snapshots=input_snapshots,basis=basis))
    for diagnostic,layer in [(False,'strict_non_st'),(True,'supplier_diagnostic')]:
        for metric,name,unit,polarity in LIMIT_RULES:
            reasons=common+limit_comparison(previous_limits,limits,metric)
            for row,expected in ((limits,day),(previous_limits,previous_day)):
                if row and str(row.get('trade_date'))!=str(expected): reasons.append('limit_date_mismatch')
            if limits and limits.get('previous_trading_day')!=previous_day: reasons.append('limit_previous_session_mismatch')
            reasons+=limit_quality(limits,metric,diagnostic)+limit_quality(previous_limits,metric,diagnostic)
            value,parts=limit_value(limits,metric,diagnostic);prior,old_parts=limit_value(previous_limits,metric,diagnostic)
            relevant=('up','broken') if metric=='broken_limit_rate' else ('up',)
            source=dict(provider=(limits or {}).get('provider'),kind='E2_supplier_diagnostic' if diagnostic else 'E2_strict_non_ST',
                current_pools={k:(limits or {}).get('pools',{}).get(k) for k in relevant},
                previous_pools={k:(previous_limits or {}).get('pools',{}).get(k) for k in relevant})
            basis=dict(formula='count(confirmed_events)' if metric=='limit_up_count' else 'numerator/denominator',
                       current=parts,previous=old_parts,cohort_status=(limits or {}).get(metric,{}).get('cohort_status'))
            if metric.startswith('promotion_'):
                basis['queue_quality']={label:{k:(row or {}).get(metric,{}).get(k) for k in
                    ('cohort_status','cohort_reasons','counts','unresolved_count','fixed_denominator','lower_bound','upper_bound')}
                    for label,row in [('current',limits),('previous',previous_limits)]}
            signals.append(signal(metric,name,unit,polarity,layer,value,prior,reasons,day=day,
                previous_day=previous_day,source=source,snapshot_id=snapshot_id,input_snapshots=input_snapshots,basis=basis))
    counts={}
    for label,items in [('formal',[s for s in signals if s['layer']!='supplier_diagnostic']),
                        ('supplier_diagnostic',[s for s in signals if s['layer']=='supplier_diagnostic'])]:
        counter=Counter(s['direction'] for s in items)
        counts[label]={d:counter[d] for d in ('positive','negative','neutral','unknown')}
    return dict(trade_date=day,previous_trading_day=previous_day,signals=signals,counts=counts,
                coverage_ratio=None,universe_status='unknown',rule_version=RULE_VERSION)
