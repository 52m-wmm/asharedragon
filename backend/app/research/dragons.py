"""Post-event cycle outcomes. Unknown boundaries never become failed promotions."""
from datetime import datetime, time, timezone
from math import sqrt
from zoneinfo import ZoneInfo

VERSION='dragon-cycle-v1.1'
SH=ZoneInfo('Asia/Shanghai')


def eod(day):
    return datetime.combine(day,time(16,30),SH).astimezone(timezone.utc)


def cycles(code, calendar, facts, events, *, require_event=True):
    """ONE stock, exact exchange calendar including warm-up and right boundary.

    Input facts already come from historical-l1-v1. We aggregate them, not derive
    limit prices. Explicit events corroborate every counted up day. Warm-up can
    establish a negative boundary but cannot seed an unobserved positive streak.
    All outputs remain diagnostic regardless of provider-reported height.
    require_event=False is an explicit price-only diagnostic population, never
    evidence for formal publication. It does not synthesize source events.
    """
    if calendar!=sorted(set(calendar)):raise ValueError('Invalid exchange calendar')
    result=[];active=None;previous=None
    def finish(boundary,reason):
        nonlocal active
        if active is None:return
        if not active['progress']:
            active=None
            return
        complete=active['left_boundary_verified'] and boundary is not None
        active.update(cycle_complete=complete,right_boundary_date=boundary,
                      max_height=active['observed_height'] if complete else None,
                      outcome_known_at_utc=eod(boundary) if boundary else None,
                      quality='diagnostic_complete' if complete else reason,
                      label_version=VERSION,formal_eligible=False)
        for n in (1,3,5,7,10):
            active[f'reached_{n}']=True if active['left_boundary_verified'] and active['observed_height']>=n else False if complete else None
        active['milestone_dates']={str(n):active['progress'][n-1]['trade_date'] for n in (1,3,5,7,10) if active[f'reached_{n}'] is True}
        result.append(active);active=None
    for day in calendar:
        fact=facts.get(day,{});kind=fact.get('classification','unknown');event=events.get(day)
        if require_event and fact.get('in_target') and kind=='up' and event is None:kind='unknown'
        if kind=='up' and fact.get('in_target'):
            if active is None:
                left=previous in ('not_up','no_limit','suspended')
                active=dict(stock_code=code,first_observed_date=day,first_board_date=day if left else None,
                    left_boundary_verified=left,observed_height=0,progress=[])
            h=active['observed_height']+1 if active['left_boundary_verified'] else None
            reported=event.get('consecutive_limit_days') if event else None
            if h is not None and reported is not None and h!=reported:
                finish(None,'vendor_height_conflict');previous='unknown';continue
            active['observed_height']+=1
            active['progress'].append(dict(trade_date=day,height_asof=h,provider_height=reported,
                available_proxy_utc=eod(day),source_observed_at_utc=event.get('observed_at') if event else None))
        elif kind in ('not_up','no_limit','suspended'):
            finish(day,'left_boundary_unknown')
        else:
            finish(None,'right_boundary_unknown')
        previous=kind
    finish(None,'right_censored')
    return result


def window_features(rows,n):
    """Raw pre-event windows, never across missing exchange sessions or actions.

    Use n+1 closes for n session changes. Reference/previous-close disagreement
    blocks price changes; source adj_factor is deliberately never used.
    """
    values=rows[-(n+1):]
    complete=len(values)==n+1 and all(r.get('close') is not None and r.get('volume') is not None and r.get('amount') is not None for r in values)
    out={'sessions_requested':n,'observed':sum(r.get('close') is not None for r in values),'complete':complete,
         'raw_price_change':None,'volume_ratio':None,'mean_amount':None,'reason':'missing_sessions'}
    if not complete:return out
    recent=values[1:]
    out['mean_amount']=sum(float(r['amount']) for r in recent)/n
    first,last=recent[0]['volume'],recent[-1]['volume']
    out['volume_ratio']=float(last)/float(first)-1 if first and float(first)>0 else None
    bridge_ok=all(r.get('pre_close') is not None and abs(float(r['pre_close'])-float(prev['close']))<=.011 for prev,r in zip(values,values[1:]))
    out['reason']='raw_no_observed_reference_break_not_certified_total_return' if bridge_ok else 'reference_break_or_missing'
    if bridge_ok and float(values[0]['close'])>0:out['raw_price_change']=float(values[-1]['close'])/float(values[0]['close'])-1
    return out


def wilson(success,total):
    if not total:return None
    z=1.95996398454;p=success/total;d=1+z*z/total
    center=(p+z*z/(2*total))/d;half=z*sqrt(p*(1-p)/total+z*z/(4*total*total))/d
    return [max(0,center-half),min(1,center+half)]


def research_state(row, *, publication_accepted=False):
    """Record-level status is distinct from unknown final height on a known start.

    A legacy formal boolean alone cannot authorize publication. Every approval is
    explicit, per interval/record, and the snapshot must be accepted as well.
    """
    approval=row.get('acceptance') or {}
    if (publication_accepted and row.get('formal_eligible') is True
        and row.get('cycle_complete') is True and row.get('first_board_date')
        and row.get('max_height') is not None
        and all(approval.get(k) is True for k in ('sources_verified','rules_validated','coverage_complete','strict_facts_complete'))):
        return 'verified'
    return 'diagnostic' if row.get('first_board_date') else 'unknown'
