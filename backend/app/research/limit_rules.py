"""Evidence-gated EOD limits. No adjusted prices, network or database writes.

Diagnostic assumptions are explicit and can never authorize formal labels.
"""
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from app.analytics.reconstruction import board_for

from app.analytics.rules import regular_rates, rounded_limit_price

VERSION = 'historical-l1-v1.2'
RULE_END = date(2026, 11, 7)
FACTOR_POLICY = {
    'adj_factor': 'quarantined_unverified',
    'formal_adjusted_returns_allowed': False,
    'adjusted_moving_averages_allowed': False,
    'backtest_factor_use_allowed': False,
    'raw_ohlc_allowed': True,
    'observed_events_allowed': True,
    'audit': 'docs/price_adjustment_audit.md',
}


def require_certified_factors():
    raise ValueError('adj_factor is quarantined: corporate actions and factor versions are unverified')


def number(value):
    try:
        n = Decimal(str(value))
        return n if n.is_finite() and n > 0 else None
    except (InvalidOperation, ValueError):
        return None


def reconstruct(row, evidence, *, diagnostic=False):
    """evidence contains independently sourced state, never the reference event label.

    official_prices: reconciled trusted observations with value/source identifiers.
    special_status: regular / no_limit / suspended / unknown.
    Missing evidence may be explored diagnostically, with formal_eligible=False.
    """
    assumptions = []
    def out(kind, reason, price=None, rate=None):
        return dict(classification=kind, reason=reason, limit_price=price, rate=rate,
                    assumptions=assumptions.copy(), formal_eligible=(not diagnostic and kind in ('up','not_up','no_limit','suspended')),
                    rule_version=VERSION)
    def need(ok, reason):
        if ok: return True
        if diagnostic:
            assumptions.append(reason)
            return True
        return False
    if row is None:
        if evidence.get('conflict'): return out('unknown', 'source_conflict')
        if evidence.get('trade_status') is False or evidence.get('special_status') == 'suspended':
            if evidence.get('event_present'): return out('unknown', 'suspended_event_conflict')
            return out('suspended', 'verified_suspension_without_price_bar')
        return out('unknown', 'daily_missing')
    day = row['trade_date']
    if row.get('adjustment_type', row.get('adjustment')) != 'none':
        return out('unknown', 'unadjusted_basis_unverified')
    if evidence.get('conflict'): return out('unknown', 'source_conflict')
    listed, delisted = evidence.get('listing_date'), evidence.get('delisting_date')
    if (listed and day < listed) or (delisted and day > delisted):
        return out('unknown', 'outside_lifecycle')
    state = evidence.get('special_status')
    if evidence.get('trade_status') is False or state == 'suspended':
        if evidence.get('event_present'): return out('unknown', 'suspended_event_conflict')
        return out('suspended', 'verified_suspension')
    if not need(evidence.get('trade_status') is True, 'historical_trading_status_missing'):
        return out('unknown', 'historical_trading_status_missing')
    board = board_for(row['stock_code'])
    session = evidence.get('listing_session')
    registered = bool(listed) and (board == 'star' or (board == 'chinext' and listed >= date(2020,8,24)) or (board == 'main' and listed >= date(2023,4,10)))
    ipo_no_limit = registered and session is not None and 1 <= session <= 5
    prices = evidence.get('official_prices', [])
    if any(not p.get('source') for p in prices): return out('unknown', 'official_price_provenance_missing')
    values = [number(p.get('value')) for p in prices]
    if any(v is None or v != v.quantize(Decimal('.01')) for v in values):
        return out('unknown', 'invalid_official_price')
    if len(set(values)) > 1: return out('unknown', 'official_price_conflict')
    if state == 'no_limit' or ipo_no_limit:
        if prices: return out('unknown', 'no_limit_price_conflict')
        return out('no_limit', 'registration_first_five_sessions' if ipo_no_limit else 'verified_no_price_limit')
    ohlc = [number(row.get(k)) for k in ('open','high','low','close')]
    if any(v is None or v != v.quantize(Decimal('.01')) for v in ohlc):
        return out('unknown', 'invalid_ohlc')
    op, high, low, close = ohlc
    if low > min(op,close) or high < max(op,close) or low > high:
        return out('unknown', 'invalid_ohlc_order')
    rate = None
    if values:
        price = values[0]  # Explicit historical daily limit is stronger than rate inference.
    else:
        if board not in ('main','chinext','star') or not date(2017,1,1) <= day <= RULE_END:
            return out('unknown', 'rule_outside_verified_scope')
        if listed is None: return out('unknown', 'listing_date_missing')
        if session is None or session < 1: return out('unknown', 'listing_session_missing')
        if session == 1: return out('unknown', 'legacy_ipo_or_relisting_day')
        if delisted and (delisted-day).days <= 60:
            return out('unknown', 'delisting_window_requires_official_price')
        if state not in (None,'unknown','regular','legacy_non_reform'):
            return out('unknown', 'special_session_requires_official_price')
        if not need(state in ('regular','legacy_non_reform'), 'special_session_history_unverified'):
            return out('unknown', 'special_session_history_unverified')
        is_st = evidence.get('is_st')
        # Post-reform growth/STAR rates do not depend on ST; main before reform does.
        if state == 'legacy_non_reform':
            if board != 'main': return out('unknown', 'non_reform_board_conflict')
            rate = Decimal('.05')
        else:
            options = regular_rates(board,day,is_st)
            if len(options) != 1: return out('unknown', 'historical_st_missing')
            rate = options[0]
        if not need(evidence.get('reference_verified') is True, 'ex_reference_unverified'):
            return out('unknown', 'ex_reference_unverified')
        pre = number(row.get('pre_close'))
        if pre is None or pre != pre.quantize(Decimal('.01')):
            return out('unknown', 'invalid_ex_reference')
        price = rounded_limit_price(pre,rate)
    if high > price: return out('unknown', 'ohlc_exceeds_limit')
    return out('up' if close == price else 'not_up', 'official_limit' if values else 'derived_limit', price, rate)


def reconcile_event(fact, event):
    """Preserve event evidence separately. Never use this result in self-validation."""
    if event is None: return {'status':'no_event_evidence', 'formal_eligible':False}
    if fact['classification'] != 'unknown' and (fact['classification']=='up') != (event['limit_type']=='up'):
        return {'status':'unknown', 'reason':'event_rule_conflict', 'formal_eligible':False}
    return {'status':'observed_'+event['limit_type'], 'formal_eligible':False}


def score(facts, truth):
    """truth maps (code,date) -> bool ONLY inside an explicitly scoped benchmark.
    Unobserved reference membership is never automatically a negative.
    """
    known = {k:f for k,f in facts.items() if k in truth and f['classification'] in ('up','not_up','no_limit','suspended')}
    tp=sum(truth[k] and f['classification']=='up' for k,f in known.items())
    fp=sum(not truth[k] and f['classification']=='up' for k,f in known.items())
    fn=sum(truth[k] and f['classification']!='up' for k,f in known.items())
    positives=sum(truth.values())
    unknown_positive=sum(v and k not in known for k,v in truth.items())
    return dict(tp=tp, fp=fp, fn=fn, precision=tp/(tp+fp) if tp+fp else None,
                recall=tp/(tp+fn) if tp+fn else None, unknown=len(truth)-len(known),
                unknown_positive=unknown_positive, benchmark_rows=len(truth),
                classified=len(known), reference_positives=positives,
                positive_capture=tp/positives if positives else None,
                classified_coverage=len(known)/len(truth) if truth else None)


def l1_labels(facts, calendar, *, coverage_complete=False, rules_validated=False):
    """ONE stock; exact exchange calendar required including warm-up boundary.
    Missing/unknown resets knowledge, not the streak to zero. Suspension breaks this
    explicitly defined calendar-session streak. Return as-of-day milestones only.
    """
    if len({f['stock_code'] for f in facts})>1: raise ValueError('One stock per cycle')
    by_day={f['trade_date']:f for f in facts}
    if len(by_day)!=len(facts) or len(set(calendar))!=len(calendar) or calendar!=sorted(calendar):
        raise ValueError('Duplicate facts or invalid calendar')
    if set(by_day)-set(calendar): raise ValueError('Fact outside supplied calendar')
    if not coverage_complete or not rules_validated: return []
    # Entire declared interval must pass; do not silently publish a partial interval.
    if any(d not in by_day or (not by_day[d].get('formal_eligible') or by_day[d]['classification'] not in ('up','not_up','no_limit','suspended')) for d in calendar): return []
    height, start = None, None
    labels=[]
    for day in calendar:
        f=by_day[day];kind=f['classification']
        if kind == 'up':
            if height == 0: start=day
            if height is not None:
                height+=1
                tags=([f'L1_{height}'] if height in (1,2,3) else [])+[f'L1_{n}_plus' for n in (5,7,10) if height>=n]
                labels.append(dict(stock_code=f['stock_code'],trade_date=day,cycle_start=start,height=height,tags=tags,rule_version=VERSION))
        elif kind in ('not_up','no_limit','suspended'): height,start=0,None
        else: height,start=None,None
    return labels
