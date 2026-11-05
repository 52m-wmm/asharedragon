"""Conservative fallback only; official historical stk_limit takes precedence."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

def limit_price(pre_close, *, trade_date: date, board: str, is_st: bool | None,
                listing_session: int | None, registration_ipo: bool | None,
                official_price=None, exceptional=False, verified_rate=None):
    if official_price is not None: return Decimal(str(official_price))
    if exceptional or listing_session is None or is_st is None: return None
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
        # Do not extend a historical ST rule beyond the audited rule interval.
        if is_st and trade_date >= date(2025,6,27) and verified_rate is None: return None
        rate = Decimal('.05') if is_st else Decimal('.10')
    else: return None
    if verified_rate is not None: rate = Decimal(str(verified_rate))
    base = Decimal(str(pre_close))
    if base <= 0: raise ValueError('pre_close must be positive')
    return (base*(1+rate)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)

def identify_limit(close, high, official_price):
    if official_price is None: return None
    limit = Decimal(str(official_price))
    if Decimal(str(close)) == limit: return 'up'
    if Decimal(str(high)) >= limit: return 'broken'
    return None
