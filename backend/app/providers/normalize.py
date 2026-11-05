import re
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

def stock_code(value):
    text = str(value).strip().upper()
    match = re.fullmatch(r'(?:(SH|SZ|BJ))?(\d{6})(?:\.(SH|SZ|BJ))?', text)
    if not match: raise ValueError(f'Invalid A-share code: {text}')
    prefix, digits, suffix = match.groups()
    expected = 'SH' if digits.startswith('6') else 'SZ' if digits.startswith(('0','3')) else 'BJ' if digits.startswith(('4','8','92')) else None
    if expected is None or (prefix and prefix != expected) or (suffix and suffix != expected):
        raise ValueError(f'Invalid A-share exchange: {text}')
    return f'{digits}.{expected}'

def number(value):
    if value is None or str(value).strip() in ('','-','--','None','nan','NaN'): return None
    try: result = Decimal(str(value))
    except InvalidOperation: raise ValueError(f'Invalid numeric value: {value}')
    if not result.is_finite(): return None
    return result

def integer(value):
    v = number(value)
    if v is None: return None
    if v != v.to_integral_value(): raise ValueError('Expected integer')
    return int(v)

def clock_time(value):
    if value is None or str(value) in ('','0','--'): return None
    v = str(value).replace(':','').zfill(6)
    return datetime.strptime(v,'%H%M%S').time()

def day(value): return date.fromisoformat(str(value)[:10]) if '-' in str(value) else datetime.strptime(str(value),'%Y%m%d').date()

def event_ak(row, kind):
    code = stock_code(row['代码'])
    height = integer(row.get('连板数')) if kind == 'up' else None
    if height is not None and height < 1: raise ValueError('Non-positive board height')
    return dict(stock_code=code,stock_name=row.get('名称'),industry=row.get('所属行业'),
        limit_type=kind, first_limit_time=clock_time(row.get('首次封板时间',row.get('首次涨停时间'))),
        last_limit_time=clock_time(row.get('最后封板时间')),open_times=integer(row.get('炸板次数',row.get('开板次数'))),
        seal_amount=number(row.get('封板资金')),turnover_rate=number(row.get('换手率')),
        consecutive_limit_days=height,limit_price=number(row.get('涨停价')),
        is_sealed_at_close=kind=='up' if kind!='down' else None,
        data_source='akshare',amount=number(row.get('成交额')),
        total_market_cap=number(row.get('总市值')),circulating_market_cap=number(row.get('流通市值')))

def daily_ak(row, code):
    result = dict(stock_code=stock_code(code),trade_date=day(row['日期']),adjustment='none')
    mapping = {'开盘':'open','最高':'high','最低':'low','收盘':'close','成交量':'volume','成交额':'amount',
        '换手率':'turnover_rate','振幅':'amplitude','涨跌幅':'pct_change'}
    result.update({dst:number(row.get(src)) for src,dst in mapping.items()})
    # AKShare daily volume is lots (100 shares); amount already CNY.
    if result['volume'] is not None: result['volume'] *= 100
    prices = [result[k] for k in ('open','high','low','close')]
    if any(p is None or p<=0 for p in prices): raise ValueError('Missing/invalid OHLC')
    if result['high'] < max(result['open'],result['close'],result['low']) or result['low'] > min(result['open'],result['close']):
        raise ValueError('Inconsistent OHLC')
    return result
