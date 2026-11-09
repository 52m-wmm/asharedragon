"""Independent trading, limit-event, and streak evidence; no name-key joins."""
from decimal import Decimal, InvalidOperation
from app.providers.normalize import stock_code
from app.research.limit_rules import reconstruct

VERSION='trading-evidence-v0.5.4.1'


def finite(value):
    try:
        n=Decimal(str(value));return n if n.is_finite() else None
    except (InvalidOperation,ValueError,TypeError):return None


def trading_evidence(code,day,bar,*,trusted=False,is_session=False,raw_trade_status=None,
                     listing_date=None,delisting_date=None,lifecycle_trusted=False,suspension_ids=()):
    code=stock_code(code)
    ids=list(suspension_ids);reasons=[];positive=False
    if bar is not None and trusted and is_session:
        if stock_code(bar['stock_code'])!=code or bar['trade_date']!=day:
            reasons.append('code_or_date_mismatch')
        else:
            o,h,l,c=(finite(bar.get(k)) for k in ('open','high','low','close'))
            v,a=finite(bar.get('volume')),finite(bar.get('amount'))
            valid=all(x is not None and x>0 for x in (o,h,l,c)) and l<=min(o,c)<=max(o,c)<=h
            positive=valid and v is not None and v>0
            if positive and bar.get('amount') is not None:
                if a is None or a<=0 or not l-Decimal('.01')<=a/v<=h+Decimal('.01'):
                    positive=False;reasons.append('volume_amount_or_unit_conflict')
            if not positive and not reasons:reasons.append('missing_invalid_or_zero_volume_bar')
    else:reasons.append('untrusted_missing_bar_or_non_session')
    outside=lifecycle_trusted and ((listing_date is not None and day<listing_date) or (delisting_date is not None and day>delisting_date))
    suspended=bool(ids) or (trusted and raw_trade_status is False)
    conflict=bool(positive and (suspended or outside)) or (outside and suspended)
    state=('trading_unknown' if conflict else 'not_listed_confirmed' if outside else 'suspended_confirmed' if suspended else 'traded_observed' if positive else 'trading_unknown')
    return dict(state=state,raw_trade_status=raw_trade_status,conflict=conflict,
        reasons=['trading_evidence_conflict'] if conflict else reasons if state=='trading_unknown' else [],
        source_ids=ids,bar_source=bar.get('source') if bar else None,
        statement='Observed turnover does not establish non-ST or a limit price')


def limit_evidence(bar,evidence,trading,events=()):
    """Events are trusted, dated direct closing-status observations, not rule truth.
    Rule arithmetic and direct observations are independent routes, with conflicts
    quarantined. Missing event membership is NEVER a negative observation.
    """
    usable=[e for e in events if e.get('trusted') is True and e.get('evidence_id') and bar is not None
            and e.get('stock_code')==bar['stock_code'] and e.get('trade_date')==bar['trade_date']]
    directions={e['is_limit_up'] for e in usable}
    conflict=trading['conflict'] or evidence.get('conflict',False) or len(directions)>1
    if trading['state'] in ('suspended_confirmed','not_listed_confirmed') and True in directions:conflict=True
    if usable and bar:
        close,high=finite(bar['close']),finite(bar['high'])
        if True in directions and close!=high:conflict=True
        if any(e.get('close') is not None and finite(e['close'])!=close for e in usable):conflict=True
    ev={**evidence,'trade_status':True if trading['state']=='traded_observed' else False if trading['state']=='suspended_confirmed' else None}
    rule=reconstruct(bar,ev)
    if rule['reason'] in ('official_price_conflict','no_limit_price_conflict','ohlc_exceeds_limit','outside_lifecycle'):
        conflict=True
    if rule['classification'] in ('up','not_up','no_limit') and directions and (rule['classification']=='up') not in directions:conflict=True
    # A dated reviewed regime must have a day-wide upper cap or no daily cap.
    # If capped: close < high <= cap; if uncapped: there is no closing limit-up.
    # This proves only a negative, never a limit price, ST state or positive event.
    price_values=[finite(bar.get(k)) for k in ('open','high','low','close')] if bar else []
    raw_valid=bool(price_values) and all(v is not None and v>0 and v==v.quantize(Decimal('.01')) for v in price_values)
    negative_bound=(raw_valid and bar.get('adjustment_type',bar.get('adjustment'))=='none'
        and evidence.get('daily_upper_bound_or_no_limit') is True and bool(evidence.get('rule_evidence_id'))
        and price_values[2]<=min(price_values[0],price_values[3])<=max(price_values[0],price_values[3])<=price_values[1]
        and price_values[3]<price_values[1])
    if negative_bound and (True in directions or rule['classification']=='up'):conflict=True
    status='unknown';basis='insufficient_evidence'
    if not conflict:
        if trading['state']=='traded_observed':
            if directions:status='confirmed_limit_up' if True in directions else 'confirmed_not_limit_up';basis='direct_closing_event'
            elif negative_bound:status='confirmed_not_limit_up';basis='close_below_session_high'
            elif rule['formal_eligible'] and rule['classification'] in ('up','not_up','no_limit'):
                status='confirmed_limit_up' if rule['classification']=='up' else 'confirmed_not_limit_up';basis='strict_reference_and_rules'
        elif trading['state'] in ('suspended_confirmed','not_listed_confirmed'):
            status='confirmed_not_limit_up';basis='confirmed_non_trading_state'
    return dict(status=status,basis=basis,quality='conflict' if conflict else 'confirmed' if status!='unknown' else 'unknown',
        rule_classification=rule['classification'],rule_reason=rule['reason'],
        numeric_limit_price=float(rule['limit_price']) if rule['limit_price'] is not None and rule['formal_eligible'] else None,
        evidence_ids=sorted({e['evidence_id'] for e in usable} | ({evidence['rule_evidence_id']} if negative_bound else set())),
        known_at_utc=max([str(e.get('observed_at_utc') or '') for e in usable]+([str(evidence.get('rule_reviewed_at_utc') or '')] if negative_bound else []),default=None),
        st_is_independent=True,version=VERSION)


def streak_fields(code,calendar,facts):
    """Observed N-day runs do not require a known left or right boundary.
    False milestones require an exact complete cycle; missing data is not failure.
    cycle_end_date is last up day; end_boundary_date is next confirmed non-up day.
    """
    if calendar!=sorted(set(calendar)):raise ValueError('Invalid exchange calendar')
    output=[];run=[];left=None;previous=None
    def finish(right):
        nonlocal run,left
        if not run:return
        known_left=left is not None;known_right=right is not None;n=len(run)
        def field(value,dates):
            evidence=sorted({eid for d in dates for eid in facts.get(d,{}).get('evidence_ids',[])})
            return dict(value=value,quality='confirmed' if value is not None else 'unknown',evidence_ids=evidence,
                known_at_utc=max((str(facts.get(d,{}).get('known_at_utc') or '') for d in dates),default=None) or None)
        fields={f'observed_streak_ge_{k}':field(True if n>=k else False if known_left and known_right else None,run[:k] if n>=k else run+([left] if left else [])+([right] if right else [])) for k in (3,5,7,10)}
        fields.update(exact_max_streak=field(n if known_left and known_right else None,run+([left] if left else [])+([right] if right else [])),
            first_board_date=field(run[0] if known_left else None,([left,run[0]] if left else [])),
            cycle_end_date=field(run[-1] if known_right else None,([run[-1],right] if right else [])))
        output.append(dict(stock_code=stock_code(code),first_observed_date=run[0],last_observed_date=run[-1],observed_length=n,
            left_boundary_date=left,end_boundary_date=right,fields=fields,progress=[dict(trade_date=d,observed_count_asof=i+1) for i,d in enumerate(run)],
            exact_cycle_eligible=known_left and known_right,version=VERSION))
        run=[];left=None
    for day in calendar:
        kind=facts.get(day,{}).get('status','unknown')
        if kind=='confirmed_limit_up':
            if not run:left=previous if previous is not None and facts.get(previous,{}).get('status')=='confirmed_not_limit_up' else None
            run.append(day)
        elif kind=='confirmed_not_limit_up':finish(day)
        else:finish(None)
        previous=day
    finish(None)
    return output
