"""D0/D1: explicitly scoped paired-stock breadth; E3 v1 is left intact."""
from collections import Counter
from fractions import Fraction
import hashlib
import json
from statistics import median

from app.analytics.direction_signals import finite

VERSION = 'matched-cohort-breadth-v1'
POLICY = dict(window='previous_SSE_session',scope='SSE_SZSE_observed_common_valid_prices_including_ST_unknown',
    reference='reuse_E1_verified_references_only',source='same_source_and_reference_basis_per_common_stock',
    sensitivity='exact_union_completion_bounds; opposite_sign; composition_effect_dominates; unpaired_count_ge_common_count',
    coverage_ratio=None,not_point_in_time=True)
TRUSTED_BASES = {'source_reference_adjacent_session_checked','same_response_reference_pct_checked',
                 'raw_close_change_pct_ohlc_checked'}


def valid_codes(profile):
    return {c for c,r in (profile or {}).get('stocks',{}).items() if r['price_reason']=='valid'}


def direction(value):
    return 'neutral' if value==0 else 'positive' if value>0 else 'negative'


def old_primary(reasons):
    if not reasons: return 'comparable'
    if set(reasons)&{'breadth_day_missing','comparison_profile_missing','value_missing_or_nonfinite','breadth_invalid_counts'}:
        return 'missing_usable_comparison'
    if 'price_source_or_reference_basis_changed' in reasons or 'critical_price_conflict' in reasons:
        return 'source_or_reference_change'
    if any('changed' in r for r in reasons): return 'sample_or_quality_change'
    return 'other_gate'


def diagnose(previous,current):
    a=(previous or {}).get('stocks',{});b=(current or {}).get('stocks',{})
    va=valid_codes(previous);vb=valid_codes(current);common=va&vb
    transitions=Counter();changes=[]
    for code in sorted(set(a)|set(b)):
        x=a.get(code);y=b.get(code);rx=x['price_reason'] if x else 'not_observed';ry=y['price_reason'] if y else 'not_observed'
        transitions[(rx,ry)]+=1
        source_change=bool(x and y and (x['price_source'],x['reference_basis'])!=(y['price_source'],y['reference_basis']))
        if rx!=ry or source_change or (rx not in ('valid','not_observed')) or (ry not in ('valid','not_observed')):
            categories=[]
            if not x or not y: categories.append('observation_membership_unexplained')
            if 'suspended' in (rx,ry): categories.append('observed_suspension_evidence')
            if any(r not in ('valid','not_observed','suspended') for r in (rx,ry)): categories.append('price_quality_issue')
            if source_change: categories.append('source_or_reference_change_pending_review')
            changes.append(dict(stock_code=code,previous=x,current=y,categories=categories,
                                in_common=code in common))
    def size(obs,valid): return dict(observed_count=len(obs),valid_count=len(valid))
    return dict(previous_profile_available=previous is not None,current_profile_available=current is not None,
        previous=size(a,va),current=size(b,vb),common_count=len(common),
        common_previous_ratio=len(common)/len(va) if va else None,common_current_ratio=len(common)/len(vb) if vb else None,
        observed_added_codes=sorted(set(b)-set(a)),observed_removed_codes=sorted(set(a)-set(b)),
        valid_added_codes=sorted(vb-va),valid_removed_codes=sorted(va-vb),
        quality_transitions=[dict(previous=x,current=y,count=n) for (x,y),n in sorted(transitions.items())],
        stock_changes=changes,category_counts=dict(Counter(c for x in changes for c in x['categories'])),
        attribution='absence does not prove listing/delisting/suspension; unverified causes retained')


def matched_breadth(day,previous_day,previous,current,*,snapshot_id,input_snapshots):
    a=(previous or {}).get('stocks',{});b=(current or {}).get('stocks',{})
    va=valid_codes(previous);vb=valid_codes(current);common=sorted(va&vb)
    reasons=[]
    if previous is None: reasons.append('previous_session_profile_missing')
    if current is None: reasons.append('current_session_profile_missing')
    if not previous_day: reasons.append('previous_session_unavailable')
    for p,expected in ((previous,previous_day),(current,day)):
        if p and p.get('trade_date')!=expected: reasons.append('profile_date_mismatch')
    for field in ('source_snapshot','breadth_rule_version','scope'):
        if previous and current and (not previous.get(field) or previous.get(field)!=current.get(field)):
            reasons.append(field+'_incompatible')
    if not va or not vb: reasons.append('zero_valid_sample')
    if not common: reasons.append('empty_common_sample')
    bad=[];changed=[]
    for c in common:
        x,y=a[c],b[c]
        if (not finite(x.get('daily_return')) or not finite(y.get('daily_return'))
            or x.get('price_conflict') or y.get('price_conflict')
            or not x.get('price_source') or not y.get('price_source')
            or x.get('reference_basis') not in TRUSTED_BASES or y.get('reference_basis') not in TRUSTED_BASES): bad.append(c)
        if (x['price_source'],x['reference_basis'])!=(y['price_source'],y['reference_basis']):changed.append(c)
    if bad: reasons.append('common_price_or_reference_untrusted')
    if changed: reasons.append('common_source_or_reference_change_unverified')
    result=dict(id='matched_cohort_breadth.advancing_ratio',name='共同股票样本上涨比例方向',trade_date=day,
        previous_trading_day=previous_day,rule_version=VERSION,snapshot_id=snapshot_id,input_snapshots=input_snapshots,
        source=dict(kind='E1_verified_price_classification',scope=POLICY['scope']),
        common_count=len(common),common_previous_ratio=len(common)/len(va) if va else None,
        common_current_ratio=len(common)/len(vb) if vb else None,
        common_codes_sha256=hashlib.sha256(json.dumps(common,separators=(',',':')).encode()).hexdigest(),
        status='unknown',direction='unknown',cohort_direction='unknown',calculable=False,
        value=None,previous_value=None,delta=None,unit='ratio',delta_unit='pp',
        previous=None,current=None,composition=None,reason_codes=sorted(set(reasons)),
        source_changed_codes=changed,untrusted_common_codes=bad,coverage_ratio=None,
        eligible_for_formal_counts=False)
    if reasons:
        result['reason']='共同样本暂不可比：'+', '.join(result['reason_codes'])
        return result
    def facts(stocks,codes):
        values=[stocks[c]['daily_return'] for c in codes];counts=Counter(direction(v) for v in values)
        return dict(count=len(codes),up=counts['positive'],down=counts['negative'],flat=counts['neutral'],
            advancing_ratio=counts['positive']/len(codes),median_return=median(values),
            identity_counts=dict(Counter(stocks[c].get('identity_status','unknown') for c in codes)))
    p,q=facts(a,common),facts(b,common)
    delta=Fraction(q['up']-p['up'],len(common))*100
    # Both full valid samples remain distinct. Nothing is imputed into E1.
    fa,fb=facts(a,sorted(va)),facts(b,sorted(vb))
    full_delta=(Fraction(fb['up'],len(vb))-Fraction(fa['up'],len(va)))*100
    effect=full_delta-delta
    union=len(va|vb);missing_previous=len(vb-va);missing_current=len(va-vb)
    # Hypothetical 0/1 completions bound direction sensitivity on the observed union.
    # These are not a market estimate, confidence interval, or reconstructed returns.
    known_difference=fb['up']-fa['up']
    lower=Fraction(known_difference-missing_previous,union)*100
    upper=Fraction(known_difference+missing_current,union)*100
    warnings=[]
    if delta*full_delta<0: warnings.append('full_and_common_directions_opposite')
    if (delta>0 and lower<0) or (delta<0 and upper>0) or (delta==0 and (lower<0 or upper>0)):
        warnings.append('unpaired_outcomes_can_reverse_direction')
    if (effect and abs(effect)>=abs(delta)): warnings.append('composition_effect_dominates_common_change')
    if union-len(common)>=len(common): warnings.append('unpaired_count_ge_common_count')
    result.update(calculable=True,previous=p,current=q,value=q['advancing_ratio'],previous_value=p['advancing_ratio'],
        delta=float(delta),median_return_delta_pp=(q['median_return']-p['median_return'])*100,
        cohort_direction=direction(delta),direction='unknown' if warnings else direction(delta),
        status='mixed' if 'full_and_common_directions_opposite' in warnings else 'composition_sensitive' if warnings else 'comparable',
        reason_codes=warnings,composition=dict(full_previous=fa,full_current=fb,full_delta_pp=float(full_delta),
            common_delta_pp=float(delta),composition_effect_pp=float(effect),
            previous_composition_gap_pp=float((Fraction(fa['up'],len(va))-Fraction(p['up'],len(common)))*100),
            current_composition_gap_pp=float((Fraction(fb['up'],len(vb))-Fraction(q['up'],len(common)))*100),
            union_count=union,unpaired_count=union-len(common),missing_previous=missing_previous,missing_current=missing_current,
            union_completion_delta_bounds_pp=[float(lower),float(upper)],
            bounds_kind='hypothetical_binary_completion_sensitivity_not_imputation_or_confidence_interval'))
    result['reason']=(f'同一组 {len(common)} 只有效价格股票，上涨 {p["up"]}→{q["up"]}，变化 {float(delta)} pp；'
                      '仅描述共同样本，不代表全市场。'+('构成敏感：'+', '.join(warnings) if warnings else ''))
    return result
