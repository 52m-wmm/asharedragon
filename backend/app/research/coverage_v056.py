"""Independent outcome windows and scoped research samples; unknown is not failure."""
from datetime import date

VERSION='dragon-coverage-v0.5.6'


def first_board_sample(run,calendar,facts):
    fields=run['fields'];start=fields['first_board_date']['value']
    if isinstance(start,str):start=date.fromisoformat(start)
    dates=[];missing=[];window_complete=False
    if start is not None:
        i=calendar.index(start);dates=calendar[i:i+5]
        missing=[d for d in dates if facts.get(d,{}).get('status','unknown')=='unknown']
        window_complete=len(dates)==5 and not missing
    if start is None:category='first_board_unknown'
    elif fields['observed_streak_ge_5']['value'] is True:category='confirmed_success_ge5'
    elif run['exact_cycle_eligible'] and run['observed_length']<5 and window_complete:category='confirmed_failure_below5'
    else:category='outcome_unknown'
    return dict(stock_code=run['stock_code'],first_board_date=start,
        category=category,observation_sessions=dates,missing_observation_dates=missing,
        observation_window_complete=window_complete,
        outcome_eligible=category in ('confirmed_success_ge5','confirmed_failure_below5'),
        local_research_eligible=category in ('confirmed_success_ge5','confirmed_failure_below5') and window_complete and run['exact_cycle_eligible'],
        formal_year_statistics_eligible=False,
        outcome_definition='Reach five consecutive exchange sessions closing limit-up in this cycle; first-board-inclusive five-session window',
        evidence_status='confirmed' if category.startswith('confirmed_') else 'unknown',label_version=VERSION)


def boundary_gaps(run,calendar,facts):
    a=run['first_observed_date'];b=run['last_observed_date']
    if isinstance(a,str):a=date.fromisoformat(a)
    if isinstance(b,str):b=date.fromisoformat(b)
    left=calendar.index(a)-1;right=calendar.index(b)+1;result=[]
    for field,idx in [('first_board_date',left),('cycle_end_date',right)]:
        if run['fields'][field]['value'] is not None:continue
        d=calendar[idx] if 0<=idx<len(calendar) else None;f=facts.get(d,{})
        result.append(dict(field=field,trade_date=d,stock_code=run['stock_code'],reason=f.get('reason','outside_scanned_calendar' if d is None else 'missing_stock_day'),
            status=f.get('status','unknown'),minimal_evidence='可信逐日收盘事件，或独立限价与原价；若仍涨停则向外延伸，直至确认非涨停边界',
            st_required_for_direct_event=False))
    return result
