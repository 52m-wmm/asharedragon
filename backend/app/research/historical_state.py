"""Independent dated state evidence. Missing and contradictory observations stay unknown."""
from datetime import date
import math
from app.providers.baostock import from_vendor


def normalize_status(row, *, source, observed_at, evidence_id):
    values={}
    for key,target in [('isST','is_st'),('tradestatus','trade_status')]:
        value=row.get(key)
        if value not in ('0','1','',None):raise ValueError(f'Invalid {key}')
        values[target]=None if value in ('',None) else value=='1'
    return dict(stock_code=from_vendor(row['code']),trade_date=date.fromisoformat(row['date']),
                source=source,observed_at_utc=observed_at,evidence_id=evidence_id,**values)


def resolve_state(observations,day,listing_date=None,delisting_date=None):
    conflict=any(len({r[k] for r in observations if r.get(k) is not None})>1 for k in ('is_st','trade_status'))
    outside=bool((listing_date and day<listing_date) or (delisting_date and day>delisting_date))
    # An observation claiming trading outside the catalogue lifecycle is a conflict.
    conflict=conflict or (outside and any(r.get('trade_status') is True for r in observations))
    values={k:next((r[k] for r in observations if r.get(k) is not None),None) if not conflict else None for k in ('is_st','trade_status')}
    state=('unknown' if conflict else 'outside_lifecycle' if outside else 'suspended' if values['trade_status'] is False else
           'risk_warning' if values['is_st'] is True else 'normal_trading' if values['trade_status'] is True and values['is_st'] is False else 'unknown')
    quality='conflict' if conflict else ('observed_time_unknown' if any(not r.get('observed_at_utc') for r in observations) else 'observed') if observations else 'catalogue' if outside else 'missing'
    return dict(**values,state=state,quality=quality,
                conflict=conflict,evidence_ids=sorted({r['evidence_id'] for r in observations}),
                sources=sorted({r['source'] for r in observations}))


def reference_evidence(bar, observations):
    """Corroborate cached same-day unadjusted prices; never use lag(close)."""
    valid=[r for r in observations if all(math.isfinite(r[k]) and r[k]>0 for k in ('open','high','low','close','pre_close'))]
    if not valid or bar is None:return bar,False,False,[]
    conflict=any(any(abs(float(bar[k])-r[k])>.001 for k in ('open','high','low','close')) or
                 (bar.get('pre_close') is not None and abs(float(bar['pre_close'])-r['pre_close'])>.001) for r in valid)
    conflict=conflict or len({r['pre_close'] for r in valid})>1
    ids=sorted({r['evidence_id'] for r in valid})
    if conflict:return bar,False,True,ids
    return {**bar,'pre_close':valid[0]['pre_close']},True,False,ids
