"""Conservative fallback only; official historical stk_limit takes precedence."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

# Effective dates: docs/historical_limit_rule_sources.md (exchange rules).
MAIN_ST_REFORM = date(2026, 7, 6)
CHINEXT_REFORM = date(2020, 8, 24)


def regular_rates(board, trade_date, is_st):
    """Regular sessions only; callers must exclude IPO/suspension/special sessions."""
    if board == 'bse': return (Decimal('.30'),) if trade_date >= date(2021,11,15) else ()
    if board == 'star' or (board == 'chinext' and trade_date >= CHINEXT_REFORM): return (Decimal('.20'),)
    if board not in ('main', 'chinext'): return ()
    if board == 'main' and trade_date >= MAIN_ST_REFORM: return (Decimal('.10'),)
    if is_st is None: return (Decimal('.05'), Decimal('.10'))
    return (Decimal('.05') if is_st else Decimal('.10'),)


def rounded_limit_price(reference, rate):
    base = Decimal(str(reference))
    if not base.is_finite() or base <= 0: raise ValueError('pre_close must be positive and finite')
    return max(base + Decimal('.01'), (base * (1 + Decimal(str(rate)))).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def limit_price(pre_close, *, trade_date: date, board: str, is_st: bool | None,
                listing_session: int | None, registration_ipo: bool | None,
                official_price=None, exceptional=False, verified_rate=None):
    if official_price is not None: return Decimal(str(official_price))
    if exceptional or listing_session is None: return None
    if listing_session < 1: raise ValueError('listing_session must be positive')
    if board == 'bse':
        if trade_date < date(2021,11,15) or listing_session == 1: return None
        rate = Decimal('.30')
    elif board == 'star' or (board == 'chinext' and trade_date >= date(2020,8,24)):
        if registration_ipo is None: return None
        if registration_ipo and listing_session <= 5: return None
        rate = Decimal('.20')
    elif board in ('main','chinext'):
        if registration_ipo is None: return None
        if (registration_ipo and listing_session <= 5) or listing_session == 1: return None
        rate = None
    else: return None
    candidates = regular_rates(board, trade_date, is_st)
    if verified_rate is not None: rate = Decimal(str(verified_rate))
    elif len(candidates) == 1: rate = candidates[0]
    else: return None
    return rounded_limit_price(pre_close, rate)


def identify_limit(close, high, official_price):
    if official_price is None: return None
    limit = Decimal(str(official_price))
    if Decimal(str(close)) == limit: return 'up'
    if Decimal(str(high)) >= limit: return 'broken'
    return None
