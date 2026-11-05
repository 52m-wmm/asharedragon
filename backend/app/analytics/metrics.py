from collections import Counter

def ratio(numerator, denominator):
    return numerator / denominator if denominator else None

def sentiment(up, broken, down, previous):
    """None means unavailable, [] means an independently confirmed empty universe."""
    result = {'limit_up_count':None if up is None else len(up),
              'broken_limit_count':None if broken is None else len(broken),
              'limit_down_count':None if down is None else len(down),
              'broken_limit_rate':None,'second_board_promotion_rate':None,'third_board_promotion_rate':None}
    if up is not None and broken is not None:
        result['broken_limit_rate'] = ratio(len(broken),len(up)+len(broken))
    valid_heights = up is not None and all(x.get('consecutive_limit_days') is not None for x in up)
    heights = Counter(x['consecutive_limit_days'] for x in up) if valid_heights else None
    result.update({f'{name}_board_count': heights.get(n,0) if heights is not None else None
                   for n,name in [(1,'first'),(2,'second'),(3,'third')]})
    result['fourth_plus_board_count'] = sum(v for k,v in heights.items() if k>=4) if heights is not None else None
    result['max_consecutive_boards'] = max(heights,default=0) if heights is not None else None
    if valid_heights and previous is not None and all(x.get('consecutive_limit_days') is not None for x in previous):
        today = {x['stock_code']:x['consecutive_limit_days'] for x in up}
        for level,name in [(1,'second'),(2,'third')]:
            cohort = [x['stock_code'] for x in previous if x['consecutive_limit_days']==level]
            result[f'{name}_board_promotion_rate'] = ratio(sum(today.get(code)==level+1 for code in cohort),len(cohort))
    return result

def consecutive(flags):
    """One entry per exchange session; None is a gap, False breaks a streak."""
    result=[]; count=0
    for flag in flags:
        if flag is None: count=None
        elif not flag: count=0
        elif count is not None: count+=1
        result.append(count)
    return result

def returns(prices, index):
    """Closing-price outcomes, complete horizons only. Use consistent adjusted prices."""
    out={}; base=prices[index]
    for n in (5,10,20):
        past=prices[index-n:index+1] if index>=n else []
        future=prices[index+1:index+n+1]
        out[f'past_return_{n}d']=base/past[0]-1 if len(past)==n+1 and all(x is not None and x>0 for x in past) else None
        out[f'max_gain_{n}d']=max(future)/base-1 if len(future)==n and base and all(x is not None and x>0 for x in future) else None
    horizon=prices[index:index+21]
    out['max_drawdown_20d']=None
    if len(horizon)==21 and all(x is not None and x>0 for x in horizon):
        peak=horizon[0]; drawdown=0
        for value in horizon:
            peak=max(peak,value); drawdown=min(drawdown,value/peak-1)
        out['max_drawdown_20d']=drawdown
    return out
