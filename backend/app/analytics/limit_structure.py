"""Supplier observations and conservative non-ST queues are separate products."""
import math
import re

from app.analytics.metrics import ratio, sentiment

VERSION = 'limit-structure-v1'
STATES = ('promoted','confirmed_not_promoted','suspended','identity_unknown','price_missing','boundary_unknown')


def height(event):
    value = event.get('consecutive_limit_days')
    return int(value) if not isinstance(value,bool) and isinstance(value,(int,float)) and math.isfinite(value) and value>=1 and int(value)==value else None


def pool(provider, day, kind, events, coverage, *, scope_verified=False, empty_verified=False):
    """A successful fetch is not proof of source scope or an independently empty pool."""
    reason = None
    if not coverage or coverage.get('status')!='success':
        reason='coverage_missing'
    elif coverage.get('row_count')!=len(events):
        reason='coverage_count_mismatch'
    elif not events and not empty_verified:
        reason='empty_not_independently_confirmed'
    elif len({e['stock_code'] for e in events})!=len(events):
        reason='duplicate_codes'
    elif len({e['raw_id'] for e in events})>1:
        reason='mixed_raw_revisions'
    elif any(e['provider']!=provider or e['trade_date']!=day or e['limit_type']!=kind for e in events):
        reason='source_or_date_mismatch'
    elif any(e.get('is_sealed_at_close') is not (kind=='up') for e in events) and kind in ('up','broken'):
        reason='closing_state_unknown_or_conflicting'
    scoped=[dict(e,consecutive_limit_days=height(e)) for e in events
            if re.fullmatch(r'(60|68)[0-9]{4}\.SH|(00|30)[0-9]{4}\.SZ',e['stock_code'])]
    return dict(provider=provider,trade_date=day,kind=kind,records=None if reason else scoped,
                status='unavailable' if reason else 'supplier_observed',reason=reason,
                scope_verified=scope_verified,scope='SSE_SZSE_A_shares',
                raw_ids=sorted({e['raw_id'] for e in events}),excluded_outside_scope=len(events)-len(scoped))


def identity(fact):
    return fact.get('identity_status','unknown')


def strict_issues(fact, *, boundary=False):
    issues=[]
    if fact.get('trade_status') is False: issues.append('suspended')
    if identity(fact)!='confirmed_non_st':
        issues.append('qualification_changed' if identity(fact)=='confirmed_st' else 'identity_unknown')
    if fact.get('price_reason')!='valid': issues.append('price_missing')
    if fact.get('event_conflict'): issues.append('event_conflict')
    if fact.get('rule_confirmed') is not True: issues.append('rule_unconfirmed')
    if boundary and fact.get('height_confirmed') is not True: issues.append('boundary_unknown')
    return issues


def promotion(previous, current, previous_facts, current_facts, level, expected_previous_day):
    """Freeze yesterday's queue; never shrink it for today's unknown outcomes."""
    old=previous['records'];new=current['records']
    compatible=(previous['provider']==current['provider'] and previous['scope']==current['scope']
                and previous['trade_date']==expected_previous_day)
    diagnostic=None;diagnostic_n=None;candidate=[]
    if old is not None and all(height(e) is not None for e in old):
        candidate=[e for e in old if height(e)==level]
        diagnostic_n=len(candidate)
        if compatible and new is not None and all(height(e) is not None for e in new):
            today={e['stock_code']:height(e) for e in new}
            diagnostic=sum(today.get(e['stock_code'])==level+1 for e in candidate)
    cohort_reasons=[]
    if old is None: cohort_reasons.append('previous_pool_unavailable')
    if not compatible: cohort_reasons.append('source_scope_or_previous_session_mismatch')
    if not previous['scope_verified']: cohort_reasons.append('previous_source_scope_unverified')
    if old is not None:
        for e in old:
            fact=previous_facts.get(e['stock_code'],{})
            if identity(fact)=='confirmed_st': continue
            if height(e) is None or (height(e)==level and strict_issues(fact,boundary=True)):
                cohort_reasons.append('previous_identity_price_or_boundary_unverified')
    cohort_reasons=sorted(set(cohort_reasons))
    queue=[e for e in candidate if identity(previous_facts.get(e['stock_code'],{}))!='confirmed_st']
    members=[];today={e['stock_code']:e for e in new or []}
    for event in queue:
        code=event['stock_code'];fact=current_facts.get(code,{});target=today.get(code)
        reasons=strict_issues(fact,boundary=target is not None)
        if new is None: reasons.append('current_pool_unavailable')
        if not current['scope_verified']: reasons.append('current_source_scope_unverified')
        if not compatible: reasons.append('source_scope_or_previous_session_mismatch')
        if target and height(target)!=level+1: reasons.append('height_transition_conflict')
        if not target and fact.get('is_limit_up') is not False:
            reasons.append('absence_is_not_confirmed_failure')
        if 'suspended' in reasons: state='suspended'
        elif 'identity_unknown' in reasons or 'qualification_changed' in reasons: state='identity_unknown'
        elif 'price_missing' in reasons: state='price_missing'
        elif reasons: state='boundary_unknown'
        else: state='promoted' if target else 'confirmed_not_promoted'
        members.append(dict(stock_code=code,state=state,reasons=sorted(set(reasons)),
            previous_height=height(event),today_height=height(target) if target else None,
            previous_raw_id=event['raw_id'],today_raw_id=target['raw_id'] if target else fact.get('event_raw_id'),
            event_source=fact.get('event_source'),reference_basis=fact.get('reference_basis'),
            identity_status=identity(fact),price_reason=fact.get('price_reason','missing'),
            first_board_date=fact.get('first_board_date')))
    counts={state:sum(m['state']==state for m in members) for state in STATES}
    n=None if cohort_reasons else len(queue)
    unresolved=sum(counts[s] for s in STATES[2:])
    available=n is not None and n>0 and unresolved==0
    if n is not None: assert sum(counts.values())==n
    return dict(status='confirmed' if available else 'unavailable',
        cohort_status='unavailable' if cohort_reasons else 'confirmed',cohort_reasons=cohort_reasons,
        reason=None if available else 'cohort_unavailable' if cohort_reasons else 'zero_denominator' if n==0 else 'unresolved_members',
        fixed_denominator=n,promoted_count=counts['promoted'] if n is not None else None,
        unresolved_count=unresolved if n is not None else None,counts=counts,members=members,
        rate=ratio(counts['promoted'],n) if available else None,
        lower_bound=ratio(counts['promoted'],n),upper_bound=ratio(counts['promoted']+unresolved,n),
        interval_kind='unresolved_outcome_bounds_not_confidence_interval',
        supplier_diagnostic=dict(status='diagnostic' if diagnostic is not None else 'unavailable',
            fixed_denominator=diagnostic_n,promoted_count=diagnostic,rate=ratio(diagnostic,diagnostic_n) if diagnostic is not None else None,
            cohort_codes=[e['stock_code'] for e in candidate],
            outside_previous_cohort_codes=sorted(code for code,e in today.items() if height(e)==level+1
                and code not in {p['stock_code'] for p in candidate}) if diagnostic is not None else None,
            matches=[dict(stock_code=e['stock_code'],previous_raw_id=e['raw_id'],
                today_raw_id=today[e['stock_code']]['raw_id'] if e['stock_code'] in today else None,
                today_height=height(today[e['stock_code']]) if e['stock_code'] in today else None,
                matched=(e['stock_code'] in today and height(today[e['stock_code']])==level+1) if diagnostic is not None else None)
                for e in candidate]))


def structure(up, broken, down, facts):
    u,b,d=(p['records'] for p in (up,broken,down))
    same_source=all(p['provider']==up['provider'] and p['trade_date']==up['trade_date']
                    and p['scope']==up['scope'] for p in (broken,down))
    overlap=sorted({e['stock_code'] for e in u or []}&{e['stock_code'] for e in b or []})
    diagnostic=sentiment(u,b,d,None)
    for name in ('second_board_promotion_rate','third_board_promotion_rate'):
        diagnostic.pop(name)  # Queue-specific objects carry the two promotion rates.
    if overlap or not same_source: diagnostic['broken_limit_rate']=None
    diagnostic['break_numerator']=len(b) if u is not None and b is not None and not overlap and same_source else None
    diagnostic['break_denominator']=len(u)+len(b) if diagnostic['break_numerator'] is not None else None
    diagnostic['third_plus_board_count']=(diagnostic['third_board_count']+diagnostic['fourth_plus_board_count']
        if diagnostic['third_board_count'] is not None and diagnostic['fourth_plus_board_count'] is not None else None)
    strict={};reasons={}
    for name,p in [('up',up),('broken',broken),('down',down)]:
        events=p['records'];problems=[]
        if events is None: problems.append(p['reason'] or 'pool_unavailable')
        if not p['scope_verified']: problems.append('source_scope_unverified')
        eligible=[]
        for e in events or []:
            fact=facts.get(e['stock_code'],{})
            if identity(fact)=='confirmed_st': continue
            eligible.append(e)
            problems.extend(strict_issues(fact))
        strict[name+'_count']=len(eligible) if not problems else None
        reasons[name]=sorted(set(problems))
        if name=='up':
            heights_ok=not problems and all(height(e) is not None and facts.get(e['stock_code'],{}).get('height_confirmed') is True for e in eligible)
            for label,predicate in [('first',lambda h:h==1),('second',lambda h:h==2),('third_plus',lambda h:h>=3)]:
                strict[label+'_count']=sum(predicate(height(e)) for e in eligible) if heights_ok else None
            if not heights_ok: reasons['heights']=['height_or_eligibility_unverified']
    valid_break=not overlap and same_source and strict['up_count'] is not None and strict['broken_count'] is not None
    strict['broken_limit_rate']=ratio(strict['broken_count'],strict['up_count']+strict['broken_count']) if valid_break else None
    strict['break_numerator']=strict['broken_count'] if valid_break else None
    strict['break_denominator']=strict['up_count']+strict['broken_count'] if valid_break else None
    return dict(supplier_diagnostic=diagnostic,strict_non_st=strict,reasons=reasons,
                overlap_codes=overlap,source_compatible=same_source,
                pools={p['kind']:{k:v for k,v in p.items() if k!='records'} for p in (up,broken,down)})
