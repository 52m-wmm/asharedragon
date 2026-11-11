"""D2 evidence gates only; does not modify E1/D1 or select an active dataset."""
from collections import Counter
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from statistics import median

VERSION='reference-and-source-bridge-v1'
PRICES=('open','high','low','close')


def equal(a,b):
    if a is None or b is None:return False
    try:return abs(Decimal(str(a))-Decimal(str(b)))<=Decimal('0.000001')
    except InvalidOperation:return False


def reference_check(raw,provider):
    """Check supplied same-response economics; never lag-fill or invert adj_factor."""
    try:
        keys={'purchased':('close','pre_close','change','pct_chg'),
              'akshare':('收盘',None,'涨跌额','涨跌幅'),
              'baostock':('close','preclose',None,'pctChg')}[provider]
        close=Decimal(str(raw[keys[0]]));pct=Decimal(str(raw[keys[3]]))
        change=Decimal(str(raw[keys[2]])) if keys[2] else None
        ref=Decimal(str(raw[keys[1]])) if keys[1] else close-change
        if not all(x.is_finite() for x in (close,ref,pct)) or min(close,ref)<=0:return None,'nonpositive_or_nonfinite_reference'
        if ref!=ref.quantize(Decimal('.01')):return None,'reference_not_on_tick'
        if change is not None and abs(close-ref-change)>Decimal('.000001'):return None,'raw_change_reference_inconsistent'
        expected=(close/ref-1)*(1 if provider=='purchased' else 100)
        tolerance=Decimal('.000051') if provider=='purchased' else Decimal('.0051')
        if abs(expected-pct)>tolerance:return None,'raw_percentage_reference_inconsistent'
        return float(ref),'same_response_arithmetic_checked'
    except (KeyError,TypeError,ValueError,InvalidOperation):return None,'same_response_fields_missing_or_invalid'


def matches(evidence,row):
    return bool(evidence and evidence['valid'] and all(equal(evidence[k],row[k]) for k in PRICES)
                and equal(evidence['reference_price'],row.get('reference_price',row.get('pre_close'))))


def bridge(previous,current,old_evidence,new_evidence,previous_alternatives=(),current_alternatives=()):
    if any(e and (e.get('adjustment')!='none' or e.get('price_unit')!='CNY') for e in [old_evidence,new_evidence,*previous_alternatives,*current_alternatives]):
        return dict(status='unknown',reason='adjustment_or_price_unit_incompatible')
    if not matches(old_evidence,previous) or not matches(new_evidence,current):
        return dict(status='unknown',reason='raw_price_or_reference_not_verified')
    if previous.get('price_conflict') or current.get('price_conflict'):
        return dict(status='unknown',reason='existing_price_conflict_preserved')
    for alternatives,primary in [(previous_alternatives,old_evidence),(current_alternatives,new_evidence)]:
        if any(e['valid'] and (not all(equal(e[k],primary[k]) for k in PRICES) or not equal(e['reference_price'],primary['reference_price'])) for e in alternatives):
            return dict(status='conflict',reason='compatible_raw_sources_disagree_no_vote')
    if old_evidence['provider']==new_evidence['provider']:
        return dict(status='resolved',reason='same_provider_unadjusted_contract_and_both_raw_references_checked')
    if any(e['provider']==new_evidence['provider'] and matches(e,previous) for e in previous_alternatives) or any(e['provider']==old_evidence['provider'] and matches(e,current) for e in current_alternatives):
        return dict(status='resolved',reason='cross_provider_same_day_OHLC_and_reference_overlap')
    if equal(new_evidence['reference_price'],old_evidence['close']):
        return dict(status='resolved',reason='cross_provider_same_unadjusted_units_and_native_reference_anchor')
    return dict(status='unknown',reason='cross_provider_reference_discontinuity_without_overlap_evidence')


def repair_reference(row,primary,alternatives=(),prior_raw_close=None):
    if not matches(primary,row):return dict(status='unknown',reason='purchase_raw_price_or_reference_check_failed')
    valid=[e for e in alternatives if e['valid']]
    if any(not matches(e,row) for e in valid):return dict(status='conflict',reason='independent_reference_or_OHLC_disagrees_no_vote')
    if valid:return dict(status='repaired',reason='independent_same_day_price_and_reference_corroboration',field_status='corroborated')
    if row['root_cause']=='supplied_reference_differs_previous_close' and row['prior_price_verified']:
        return dict(status='repaired',reason='supplied_reference_change_and_fractional_pct_consistent_not_lag_fill',field_status='single_source_validated')
    if row['root_cause']=='research_start_prior_outside_U' and equal(prior_raw_close,row['pre_close']):
        return dict(status='repaired',reason='raw_archive_previous_real_session_anchor_outside_U',field_status='single_source_validated')
    return dict(status='unknown',reason='previous_stock_or_price_context_unresolved_despite_internal_arithmetic')


def confirmed_cohort(old_result,previous,current,proofs):
    """Retain D1 guards/sensitivity; replace only its unverified source-change gate."""
    result={**old_result,'rule_version':VERSION,'old_rule_version':old_result['rule_version'],
            'source':{'kind':'unchanged_E1_prices_with_D2_per_stock_source_evidence','scope':old_result['source']['scope']}}
    common=sorted(c for c in previous['stocks'].keys()&current['stocks'].keys()
                  if previous['stocks'][c]['price_reason']==current['stocks'][c]['price_reason']=='valid')
    unresolved=[c for c in common if proofs.get(c,{}).get('status')!='resolved']
    other=set(old_result['reason_codes'])-{'common_source_or_reference_change_unverified'}
    result['unresolved_common_codes']=unresolved
    if other or unresolved:
        result.update(reason_codes=sorted(other|({'unresolved_per_stock_source_bridge'} if unresolved else set())),
                      reason='D2 retains unknown; no common stock removed to manufacture comparability')
        return result
    def facts(profile,codes):
        values=[profile['stocks'][c]['daily_return'] for c in codes]
        counts=Counter('up' if v>0 else 'down' if v<0 else 'flat' for v in values)
        return dict(count=len(codes),up=counts['up'],down=counts['down'],flat=counts['flat'],
                    advancing_ratio=counts['up']/len(codes),median_return=median(values),
                    identity_counts=dict(Counter(profile['stocks'][c]['identity_status'] for c in codes)))
    a={c for c,r in previous['stocks'].items() if r['price_reason']=='valid'}
    b={c for c,r in current['stocks'].items() if r['price_reason']=='valid'}
    p,q=facts(previous,common),facts(current,common);fa,fb=facts(previous,sorted(a)),facts(current,sorted(b))
    delta=Fraction(q['up']-p['up'],len(common))*100
    full=(Fraction(fb['up'],len(b))-Fraction(fa['up'],len(a)))*100;effect=full-delta
    union=len(a|b);lower=Fraction(fb['up']-fa['up']-len(b-a),union)*100;upper=Fraction(fb['up']-fa['up']+len(a-b),union)*100
    warnings=[]
    if delta*full<0:warnings.append('full_and_common_directions_opposite')
    if (delta>0 and lower<0) or (delta<0 and upper>0) or (delta==0 and (lower<0 or upper>0)):warnings.append('unpaired_outcomes_can_reverse_direction')
    if effect and abs(effect)>=abs(delta):warnings.append('composition_effect_dominates_common_change')
    if union-len(common)>=len(common):warnings.append('unpaired_count_ge_common_count')
    direction='positive' if delta>0 else 'negative' if delta<0 else 'neutral'
    result.update(calculable=True,status='mixed' if 'full_and_common_directions_opposite' in warnings else 'composition_sensitive' if warnings else 'comparable',
        direction='unknown' if warnings else direction,cohort_direction=direction,previous=p,current=q,value=q['advancing_ratio'],previous_value=p['advancing_ratio'],delta=float(delta),
        median_return_delta_pp=(q['median_return']-p['median_return'])*100,reason_codes=warnings,
        reason='Every original common stock has a raw-backed source/reference compatibility proof; original D1 sensitivity retained',
        composition=dict(full_previous=fa,full_current=fb,full_delta_pp=float(full),common_delta_pp=float(delta),composition_effect_pp=float(effect),
                         union_count=union,unpaired_count=union-len(common),missing_previous=len(b-a),missing_current=len(a-b),union_completion_delta_bounds_pp=[float(lower),float(upper)]))
    return result


def ratio_delta_pp(before,after):
    return None if before is None or after is None else (after-before)*100
