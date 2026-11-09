"""Independent US/A-share research. Default feature access is vintage-aware.

Retrospective alignment is a separately labelled diagnostic, never a certified
point-in-time trading dataset. A-share outcomes are not pre-open predictors.
"""
from bisect import bisect_left
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import exchange_calendars as xc

UTC=timezone.utc
SHANGHAI=ZoneInfo('Asia/Shanghai')
NEW_YORK=ZoneInfo('America/New_York')
ROOT=Path.home()/'Datasets/asharedragon/cross_market'
TARGETS=('limit_up_count','first_board_count','broken_limit_rate','second_board_promotion_rate','third_board_promotion_rate','tech_limit_up_count')


def aware_utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Naive timestamp cannot establish availability')
    return value.astimezone(UTC)


def decision_cutoff(day,clock='09:30'):
    parsed=time.fromisoformat(clock)
    if parsed.tzinfo is not None:raise ValueError('Decision time must be a Shanghai wall-clock time')
    return datetime.combine(day,parsed,SHANGHAI).astimezone(UTC)


def us_schedule(start='2016-12-01',end='2026-11-07'):
    calendar=xc.get_calendar('XNAS',start=start,end=end)
    rows=[]
    for label,row in calendar.schedule.iterrows():
        close=aware_utc(row['close'].to_pydatetime())
        rows.append({'us_trade_date':label.date(),'session_open_utc':aware_utc(row['open'].to_pydatetime()),
            'session_close_utc':close,'assumed_available_at_utc':close+timedelta(hours=1),
            'exchange_timezone':'America/New_York','calendar_implementation':calendar.name})
    return rows


def price_returns(rows,schedule,cash_by_date):
    """No return across a missing scheduled session; no future adjustment anchor."""
    by_date={r['us_trade_date']:r for r in rows}
    if len(by_date)!=len(rows):raise ValueError('Duplicate US session')
    result=[]
    for i,session in enumerate(schedule):
        d=session['us_trade_date'];row=by_date.get(d)
        if row is None:continue
        previous=by_date.get(schedule[i-1]['us_trade_date']) if i else None
        cash=cash_by_date.get(d,0.)
        eligible=previous is not None and not row.get('close_disagreement') and not previous.get('close_disagreement')
        result.append({**row,**session,'previous_us_trade_date':schedule[i-1]['us_trade_date'] if i else None,
            'reported_cash_distribution':cash,
            'return_source_observed_at_utc':max(row['source_observed_at_utc'],previous['source_observed_at_utc']) if previous and row.get('source_observed_at_utc') and previous.get('source_observed_at_utc') else row.get('source_observed_at_utc'),
            'price_return':row['close']/previous['close']-1 if eligible else None,
            'cash_entitlement_return_proxy':(row['close']+cash)/previous['close']-1 if eligible else None,
            'return_quality':'checked_close_pair' if eligible else 'missing_or_conflicting_close_pair'})
    return result


def align_session(day,schedule,bars,clock='09:30',mode='strict'):
    if mode not in ('strict','retrospective'):raise ValueError('Unknown availability mode')
    cutoff=decision_cutoff(day,clock)
    available=[aware_utc(r['assumed_available_at_utc']) for r in schedule]
    if available!=sorted(available):raise ValueError('US schedule must be ordered')
    index=bisect_left(available,cutoff)-1  # strictly before cutoff, not <=
    if index<0:return {'cutoff_utc':cutoff,'status':'no_prior_us_session','price_return':None,'cash_entitlement_return_proxy':None}
    session=schedule[index];bar=bars.get(session['us_trade_date'])
    result={**session,'cutoff_utc':cutoff,'price_return':None,'cash_entitlement_return_proxy':None,
        'us_close_age_hours':(cutoff-session['session_close_utc']).total_seconds()/3600,
        'availability_basis':'assumed_close_plus_60_minutes; actual historical publication time unverified',
        'pit_verified':False}
    if bar is None:return {**result,'status':'missing_expected_us_session'}
    observed=aware_utc(bar.get('return_source_observed_at_utc') or bar['source_observed_at_utc'])
    result['source_observed_at_utc']=observed
    if mode=='strict' and observed>=cutoff:
        return {**result,'status':'historical_vintage_not_available_by_cutoff'}
    if bar['return_quality']!='checked_close_pair':
        return {**result,'status':bar['return_quality']}
    return {**result,'status':'observed_before_cutoff' if mode=='strict' else 'retrospective_unverified_vintage',
        'pit_verified':mode=='strict','price_return':bar['price_return'],
        'cash_entitlement_return_proxy':bar['cash_entitlement_return_proxy']}


def pool_metrics(up,broken,previous_up,missing_today=(),missing_previous=()):
    """None means unavailable; [] only follows a verified complete source snapshot."""
    out={k:None for k in TARGETS}
    out['broken_limit_count']=None if broken is None else len(broken)
    out['event_quality']='missing_pool'
    out['promotion_quality']='missing_previous_pool'
    if up is None or broken is None:return out
    if len({r['stock_code'] for r in up})!=len(up) or len({r['stock_code'] for r in broken})!=len(broken):
        return {**out,'event_quality':'duplicate_events'}
    if {r['stock_code'] for r in up}&{r['stock_code'] for r in broken}:
        return {**out,'event_quality':'conflicting_pool_membership'}
    out.update(limit_up_count=len(up),event_quality='provider_pool_complete',
        broken_limit_rate=len(broken)/(len(up)+len(broken)) if up or broken else None)
    if all(r.get('consecutive_limit_days') is not None for r in up):
        out['first_board_count']=sum(r['consecutive_limit_days']==1 for r in up)
    if previous_up is None:return out
    if any(r.get('consecutive_limit_days') is None for r in up+previous_up):
        return {**out,'promotion_quality':'missing_vendor_height'}
    today={r['stock_code']:r['consecutive_limit_days'] for r in up}
    missing=set(missing_today)|set(missing_previous)
    out['promotion_quality']='observed_provider_cohort_not_formal_reconstruction'
    for level,name in ((1,'second'),(2,'third')):
        cohort={r['stock_code'] for r in previous_up if r['consecutive_limit_days']==level}
        if cohort&missing:
            out['promotion_quality']='cohort_daily_gap';continue
        if cohort:out[f'{name}_board_promotion_rate']=sum(today.get(c)==level+1 for c in cohort)/len(cohort)
    # No historical industry classification is certified in the current project.
    # Never derive past technology membership from today's industry or stock name.
    return out


class USSignals:
    def __init__(self,root=ROOT):
        self.root=Path(root).resolve()
        manifest=json.loads((self.root/'manifest.json').read_text())
        self.manifest=manifest
        dataset=(self.root/manifest['dataset']).resolve()
        if not dataset.is_relative_to(self.root):raise ValueError('Unsafe US dataset path')
        self.db=duckdb.connect(config={'memory_limit':'256MB','threads':'2','TimeZone':'UTC'})
        try:
            for name in ('qqq','us_calendar','sentiment'):
                self.db.read_parquet(str(dataset/f'{name}.parquet')).create_view(name)
            self.schedule=self.query('SELECT * FROM us_calendar ORDER BY us_trade_date')
            self.bars={r['us_trade_date']:r for r in self.query('SELECT * FROM qqq')}
        except Exception:
            self.db.close()
            raise

    def query(self,sql,params=None):
        result=self.db.execute(sql,params or [])
        names=[d[0] for d in result.description]
        return [dict(zip(names,r)) for r in result.fetchall()]
    def close(self):self.db.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

    def joined(self,start,end,mode='strict',clock='09:30',page=1,page_size=200):
        if start>end or str(start)<self.manifest['ashare_start'] or str(end)>self.manifest['ashare_end']:
            raise ValueError('Outside A-share audited calendar range')
        rows=self.query('SELECT * FROM sentiment WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date LIMIT ? OFFSET ?',
            [start,end,page_size,(page-1)*page_size])
        result=[]
        for row in rows:
            qqq=align_session(row['trade_date'],self.schedule,self.bars,clock,mode)
            cutoff=qqq['cutoff_utc']
            lag_verified=(row.get('lag_source_observed_at_utc') is not None and row['lag_source_observed_at_utc']<cutoff)
            features={key:(row.get(key) if mode=='retrospective' or lag_verified else None)
                      for key in ('lag_limit_up_count','lag_first_board_count','lag_broken_limit_rate','lag5_mean_up_count')}
            result.append({'trade_date':row['trade_date'],'cutoff_utc':cutoff,
                'features':{'qqq':qqq,'ashare_lags':features,'lag_pit_verified':lag_verified,'lag_event_provider':row.get('lag_event_provider')},
                'outcomes':{k:row.get(k) for k in TARGETS},
                'outcome_available_proxy_utc':row['outcome_available_proxy_utc'],
                'outcomes_are_not_preopen_features':True,
                'quality':{k:row.get(k) for k in ('event_provider','event_quality','promotion_quality','daily_missing_count','industry_quality','event_source_observed_at_utc')},
                'nq_preopen_price':None,'nq_status':'no_verified_free_historical_intraday_source'})
        return {'data':result,'meta':{'mode':mode,'decision_timezone':'Asia/Shanghai','decision_time':clock,
            'page':page,'page_size':page_size,'strict_default_blocks_later_downloaded_vintages':True,
            'warning':'Retrospective mode is an explicitly unverified historical-vintage diagnostic, not a PIT-certified backtest.',
            'price_return_definition':'previous completed US regular session close / preceding scheduled session close - 1; not US open/previous-close overnight gap',
            'event_scope':'provider-specific observed event pools, not reconstructed full-market facts',
            'industry_quality':'no certified effective-dated historical industry mapping; technology count remains null',
            'existing_ashare_gap_policy':'retain missing flags; never impute suspension or zero return'}}
