"""BaoStock SDK boundary. Runs inside the existing killable provider subprocess."""
import importlib.metadata
import inspect
import socket
from datetime import datetime, timezone

FIELDS = 'date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,isST'
ENDPOINTS = {'query_stock_basic', 'query_all_stock', 'query_trade_dates', 'query_history_k_data_plus', 'query_daily_history_k_AStock'}


def fetch(endpoint, params):
    import baostock as bs
    socket.setdefaulttimeout(12)
    if endpoint not in ENDPOINTS:
        raise ValueError('Unsupported BaoStock endpoint')
    login = bs.login()
    if login.error_code != '0':
        raise RuntimeError(f'BaoStock login {login.error_code}: {login.error_msg}')
    try:
        fn = getattr(bs, endpoint)
        result = fn(**params)
        if result.error_code != '0':
            raise RuntimeError(f'BaoStock {result.error_code}: {result.error_msg}')
        records = []
        while result.next():
            records.append(dict(zip(result.fields, result.get_row_data())))
        from baostock.common.contants import BAOSTOCK_PER_PAGE_COUNT
        if len(result.data) == BAOSTOCK_PER_PAGE_COUNT:
            raise RuntimeError('BaoStock pagination ended without a verified final page')
        if result.error_code != '0':
            raise RuntimeError(f'BaoStock pagination {result.error_code}: {result.error_msg}')
        return {'version': importlib.metadata.version('baostock'),
                'signature': str(inspect.signature(fn)), 'columns': result.fields,
                'records': records, 'source': 'baostock'}
    finally:
        bs.logout()


def to_vendor(code):
    from app.providers.normalize import stock_code
    canonical = stock_code(code)
    return canonical[-2:].lower() + '.' + canonical[:6]


def from_vendor(code):
    from app.providers.normalize import stock_code
    exchange, digits = code.split('.')
    return stock_code(digits + '.' + exchange.upper())


def normalize_daily(row):
    from app.providers.normalize import number, day
    if row.get('adjustflag') != '3':
        raise ValueError('BaoStock reconstruction requires unadjusted data (adjustflag=3)')
    if row.get('isST') not in ('0', '1', '') or row.get('tradestatus') not in ('0', '1', ''):
        raise ValueError('Unexpected ST/trading status')
    mapping = {'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close', 'preclose': 'pre_close',
               'volume': 'volume', 'amount': 'amount', 'turn': 'turnover_rate', 'pctChg': 'pct_change',
               'peTTM': 'pe_ttm', 'pbMRQ': 'pb'}
    values = {target: number(row.get(source)) for source, target in mapping.items()}
    values.update(stock_code=from_vendor(row['code']), trade_date=day(row['date']), adjustment='none',
                  is_st=None if not row.get('isST') else row['isST'] == '1',
                  trade_status=None if not row.get('tradestatus') else row['tradestatus'] == '1')
    # BaoStock volume is shares, amount CNY: unlike AKShare do NOT multiply by 100.
    if values['trade_status']:
        prices = [values[k] for k in ('open', 'high', 'low', 'close', 'pre_close')]
        if any(p is None or p <= 0 for p in prices):
            raise ValueError('Missing/nonpositive traded OHLC or reference close')
        if values['high'] < max(values['open'], values['close'], values['low']) or values['low'] > min(values['open'], values['close']):
            raise ValueError('Inconsistent BaoStock OHLC')
    for key in ('volume', 'amount'):
        if values[key] is not None and values[key] < 0:
            raise ValueError('Negative volume/amount')
    return values
