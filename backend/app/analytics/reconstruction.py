"""Versioned, conservative EOD reconstruction. No intraday facts are inferred."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.analytics.rules import regular_rates, rounded_limit_price

VERSION = 'baostock-close-v2.1'
UNKNOWN = {'unknown', 'missing'}


def board_for(code):
    if code.endswith('.BJ'): return 'bse'
    if code.startswith('688'): return 'star'
    if code.startswith(('300', '301')): return 'chinext'
    if code.startswith(('60', '00')): return 'main'
    return None


def classify(row, listing_date, delisting_date, calendar, previous=None):
    """calendar includes listing-day history; row is a normalized immutable observation."""
    if row is None:
        return {'classification': 'missing', 'reason': 'daily_missing', 'limit_price': None}
    d = row['trade_date']
    def result(kind, reason, price=None):
        return {'classification': kind, 'reason': reason, 'limit_price': price}
    if row.get('trade_status') is False:
        return result('suspended', 'source_suspended')
    if row.get('trade_status') is None or row.get('is_st') is None:
        return result('unknown', 'historical_status_missing')
    board = board_for(row['stock_code'])
    if board is None or board == 'bse':
        return result('unknown', 'board_not_covered_by_verified_baostock_universe')
    if listing_date is None or listing_date > d:
        return result('unknown', 'listing_date_missing_or_invalid')
    # A buffer deliberately excludes the final period, rather than assuming ordinary ST rules.
    if delisting_date and (delisting_date-d).days <= 60:
        return result('unknown', 'delisting_window_unverified')
    if previous and previous.get('trade_status') is False:
        return result('unknown', 'resumption_special_rule_unverified')
    if listing_date not in calendar:
        return result('unknown', 'listing_calendar_missing')
    session = calendar[d] - calendar[listing_date] + 1
    registration = board == 'star' or (board == 'chinext' and listing_date >= date(2020,8,24)) or (board == 'main' and listing_date >= date(2023,4,10))
    if registration and session <= 5:
        return result('no_limit', 'registration_first_five_sessions')
    if session == 1:
        return result('unknown', 'legacy_ipo_or_relisting_first_day')
    rate = regular_rates(board,d,row['is_st'])[0]
    pre = row.get('pre_close')
    if pre is None or pre <= 0 or row.get('close') is None:
        return result('unknown', 'reference_price_missing')
    pre = Decimal(str(pre))
    if pre != pre.quantize(Decimal('.01')):
        return result('unknown', 'reference_price_not_on_tick')
    price = rounded_limit_price(pre,rate)
    if Decimal(str(row['close'])) > price or (row.get('high') is not None and Decimal(str(row['high'])) > price):
        return result('unknown', 'price_exceeds_rule_special_event_or_source_error', price)
    return result('up' if Decimal(str(row['close'])) == price else 'not_up', 'regular_rule_eod', price)


def streaks(facts):
    """Input is consecutive exchange sessions for ONE stock, including explicit missing rows."""
    height = None
    start = None
    for fact in facts:
        kind = fact['classification']
        if kind in UNKNOWN:
            height, start = None, None
        elif kind == 'up':
            if height == 0: start = fact['trade_date']
            if height is not None: height += 1
        else:
            height, start = 0, None
        fact['consecutive_limit_days'] = height if kind == 'up' else None
        fact['first_board_date'] = start if kind == 'up' else None
    return facts


def compare(facts, observed):
    """AKShare is a reference universe, not independently established market ground truth."""
    by_code = {f['stock_code']: f for f in facts}
    classified = {c for c,f in by_code.items() if f['classification'] not in UNKNOWN}
    predicted = {c for c in classified if by_code[c]['classification'] == 'up'}
    reference = set(observed)
    comparable_reference = reference & classified
    tp = predicted & reference
    fp = predicted-reference
    fn = comparable_reference-predicted
    tn = classified-(predicted|reference)
    unavailable = reference-classified
    def ratio(a,b): return a/b if b else None
    return {'true_positive':len(tp),'false_positive':len(fp),'false_negative':len(fn),'true_negative':len(tn),
            'accuracy':ratio(len(tp)+len(tn),len(classified)), 'precision':ratio(len(tp),len(predicted)),
            'recall':ratio(len(tp),len(comparable_reference)),
            'reference_coverage':ratio(len(comparable_reference),len(reference)),
            'reference_capture_rate':ratio(len(tp),len(reference)),
            'classified_stocks':len(classified),'reference_stocks':len(reference),
            'matched':sorted(tp),'false_positives':[{'stock_code':c,'reason':'not_in_akshare_pool; scope_or_rule_difference_unresolved'} for c in sorted(fp)],
            'false_negatives':[{'stock_code':c,'reason':by_code[c]['reason']} for c in sorted(fn)],
            'unassessable_reference':[{'stock_code':c,'reason':by_code[c]['reason'] if c in by_code else 'outside_selected_or_provider_universe'} for c in sorted(unavailable)],
            'interpretation':'准确率/召回率仅针对共同可判定样本；未覆盖不算真阴性，缺失不等于漏报。AKShare 是对照源而非绝对真值。'}
