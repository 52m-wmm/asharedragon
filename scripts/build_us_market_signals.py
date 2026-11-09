"""Build an isolated cross-market snapshot from public US feeds and READ ONLY local facts."""
from collections import Counter,defaultdict
from datetime import date,datetime,timezone,timedelta
import hashlib
import json
import math
from pathlib import Path
import shutil

import duckdb
import exchange_calendars
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from sqlalchemy import text

from app.db.session import engine
from app.collectors.tushare_history import normalize_limit
from app.providers.normalize import event_ak
from app.research.us_signals import ROOT,us_schedule,price_returns,pool_metrics,align_session,SHANGHAI,TARGETS

PROBES=Path('data/us_market_probes')
END=date(2026,11,6)
UTC=timezone.utc


def save_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str,allow_nan=False));tmp.replace(path)


def cached_nasdaq(name,endpoint,params):
    path=PROBES/name
    if not path.exists():
        response=requests.get('https://api.nasdaq.com/api/quote/QQQ/'+endpoint,params=params,
            headers={'User-Agent':'AShareDragon local academic research'},timeout=25)
        response.raise_for_status();payload=response.json()
        if not payload.get('data'):raise ValueError('Nasdaq returned no data')
        save_json(path,payload)
    payload=json.loads(path.read_text())
    if not payload.get('data'):raise ValueError('Invalid cached Nasdaq response')
    return payload,datetime.fromtimestamp(path.stat().st_mtime,UTC)


def write_records(path,rows):
    # Pandas gives nullable all-empty numeric columns a consistent Parquet schema.
    frame=pd.DataFrame(rows)
    for col in frame.columns:
        if col.endswith('_utc'):frame[col]=pd.to_datetime(frame[col],utc=True)
    pq.write_table(pa.Table.from_pandas(frame,preserve_index=False),path,compression='zstd')


def load_qqq(output):
    price,price_seen=cached_nasdaq('nasdaq_history.json','historical',{'assetclass':'etf','fromdate':'2016-12-01','todate':str(END),'limit':9999})
    dividend,div_seen=cached_nasdaq('nasdaq_dividends.txt','dividends',{'assetclass':'etf','limit':9999})
    sina_path=PROBES/'sina_raw.json'
    if not sina_path.exists():
        from app.providers.client import Client
        payload=Client('akshare',retries=0,interval=1,timeout=40).fetch('stock_us_daily',symbol='QQQ',adjust='')
        save_json(sina_path,payload)
    sina=json.loads(sina_path.read_text());sina_seen=datetime.fromtimestamp(sina_path.stat().st_mtime,UTC)
    for name in ('nasdaq_history.json','nasdaq_dividends.txt','sina_raw.json'):
        shutil.copy2(PROBES/name,output/'raw'/name)
    schedule=us_schedule();calendar={r['us_trade_date']:r for r in schedule}
    cross={date.fromisoformat(r['date'][:10]):r for r in sina['records']}
    actions=[];cash={};duplicates=set()
    for item in dividend['data']['dividends']['rows']:
        d=datetime.strptime(item['exOrEffDate'],'%m/%d/%Y').date()
        if not date(2016,12,1)<=d<=END:continue
        key=(d,item['type'],item['amount'])
        if key in duplicates:raise ValueError('Duplicate reported distribution')
        duplicates.add(key)
        if item['type']!='Cash' or item.get('currency','USD')!='USD':raise ValueError('Unrecognized corporate action')
        amount=float(item['amount'].replace('$','').replace(',',''))
        if amount<0 or d not in calendar:raise ValueError('Invalid cash action')
        cash[d]=cash.get(d,0)+amount
        parsed=lambda field: datetime.strptime(item[field],'%m/%d/%Y').date() if item.get(field) not in (None,'N/A','--') else None
        actions.append({'symbol':'QQQ','ex_date':d,'type':'cash','amount_usd_per_share':amount,
            'declaration_date':parsed('declarationDate'),'record_date':parsed('recordDate'),'payment_date':parsed('paymentDate'),
            'effective_session_open_utc':calendar[d]['session_open_utc'],'announcement_at_utc':None,
            'source_observed_at_utc':div_seen,'source':'nasdaq_public_dividends','announcement_time_verified':False})
    rows=[];differences=[];volume_differences=[]
    raw_rows=price['data']['tradesTable']['rows']
    if price['data']['symbol']!='QQQ' or int(price['data']['totalRecords'])!=len(raw_rows):raise ValueError('Wrong symbol or truncated price response')
    for item in raw_rows:
        d=datetime.strptime(item['date'],'%m/%d/%Y').date()
        if d not in calendar:raise ValueError('Unexpected US trading date')
        values={k:float(item[k].replace(',','').replace('$','')) for k in ('open','high','low','close','volume')}
        if any(not math.isfinite(v) or v<=0 for v in values.values()) or values['high']<max(values['open'],values['close'],values['low']) or values['low']>min(values['open'],values['close']):
            raise ValueError('Invalid Nasdaq OHLCV')
        if d not in cross:raise ValueError('Independent unadjusted comparison unavailable')
        delta={k:values[k]-float(cross[d][k]) for k in ('open','high','low','close')}
        if any(abs(v)>.011 for v in delta.values()):differences.append({'date':d,**delta})
        volume_differences.append(abs(values['volume']-cross[d]['volume']))
        rows.append({'symbol':'QQQ','us_trade_date':d,**values,'currency':'USD','volume_unit':'shares',
            'source':'nasdaq_public_historical','adjustment_type':'provider_ohlc_no_cash_adjustment',
            'split_policy':'no_in_window_split_in_vendor_listing; issuer_completeness_unverified',
            'close_disagreement':abs(delta['close'])>.011,'ohlc_cross_source_disagreement':any(abs(v)>.011 for v in delta.values()),
            'source_observed_at_utc':max(price_seen,sina_seen,div_seen),
            'price_downloaded_at_utc':price_seen,'actions_downloaded_at_utc':div_seen,
            'is_target_period':d>=date(2017,1,1)})
    normalized=price_returns(rows,schedule,cash)
    write_records(output/'qqq.parquet',normalized);write_records(output/'us_calendar.parquet',schedule);write_records(output/'qqq_actions.parquet',actions)
    expected={d for d in calendar if d>=date(2017,1,1)};observed={r['us_trade_date'] for r in normalized if r['is_target_period']}
    special=[r for r in actions if r['ex_date']==date(2023,12,27)]
    if len(special)!=1 or abs(special[0]['amount_usd_per_share']-.21584)>1e-9:raise ValueError('OCC special distribution crosscheck failed')
    audit={'target_rows':len(observed),'expected_sessions':len(expected),'missing_sessions':sorted(expected-observed),
        'first_date':min(observed),'last_date':max(observed),'warmup_rows':len(rows)-len(observed),
        'dividends_in_target':sum(r['ex_date'].year>=2017 for r in actions),'dividend_year_counts':dict(Counter(r['ex_date'].year for r in actions if r['ex_date'].year>=2017)),
        'cross_source_ohlc_differences':[r for r in differences if r['date'].year>=2017],
        'cross_source_close_difference_dates':[r['us_trade_date'] for r in rows if r['close_disagreement']],
        'max_volume_difference_shares':max(volume_differences),
        'volume_difference_rows':sum(v>1 for v in volume_differences),
        'missing_or_conflicting_return_rows':sum(r['price_return'] is None and r['is_target_period'] for r in normalized),
        'calendar_library':exchange_calendars.__version__,'calendar_name':'XNAS alias XNYS; standard and exceptional holidays/early closes',
        'pit_status':'downloaded historical revision; actual historical publication timestamps unavailable',
        'special_dividend_verified':{'ex_date':'2023-12-27','amount':.21584,'source':'https://infomemo.theocc.com/infomemos?number=53848'},
        'source_files':{n:hashlib.sha256((output/'raw'/n).read_bytes()).hexdigest() for n in ('nasdaq_history.json','nasdaq_dividends.txt','sina_raw.json')}}
    return normalized,schedule,audit


def event_snapshot():
    with engine.connect() as c,c.begin():
        c.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        read=lambda q:[dict(r) for r in c.execute(text(q)).mappings()]
        calendar=read("SELECT trade_date FROM trading_days WHERE exchange='SSE' AND trade_date BETWEEN '2016-12-01' AND '2026-11-06' ORDER BY trade_date")
        raw=read("SELECT id,provider,endpoint,trade_date,parameters,raw_payload,fetched_at,sha256 FROM raw_observations WHERE ingestion_status='success' AND ((provider='tushare_proxy' AND endpoint='limit_list_d') OR (provider='akshare' AND endpoint IN ('stock_zt_pool_em','stock_zt_pool_zbgc_em'))) ORDER BY id")
        parts=read("SELECT * FROM proxy_partitions WHERE endpoint='limit_list_d' AND status='success' AND error_message IS NULL ORDER BY id")
    by_id={r['id']:r for r in raw};selected={}
    for part in parts:
        if part['raw_id'] not in by_id:continue
        p=part['parameters'];kind=p.get('limit_type')
        if set(p)-{'trade_date','start_date','end_date','limit_type'} or kind not in (None,'U','Z','D'):continue
        for k in ([kind] if kind else ['U','Z','D']):selected[('tushare_proxy',part['trade_date'],k)]=(by_id[part['raw_id']],part)
    for r in raw:
        if r['provider']=='akshare' and r['trade_date']:
            kind='U' if r['endpoint']=='stock_zt_pool_em' else 'Z'
            selected[('akshare',r['trade_date'],kind)]=(r,None)
    pools={};evidence=[];issues=[]
    for key,(r,part) in selected.items():
        provider,day,kind=key
        try:
            records=r['raw_payload']['records']
            if provider=='tushare_proxy':
                day_records=[x for x in records if str(x.get('trade_date','')).replace('-','')==day.strftime('%Y%m%d')]
                if len(day_records)!=part['row_count']:raise ValueError('Partition count disagrees with raw snapshot')
                if part['audit'].get('field_anomaly_count',0):raise ValueError('Source field anomalies')
                rows=[normalize_limit(x,day) for x in day_records if x['limit']==kind]
            else:rows=[event_ak(x,'up' if kind=='U' else 'broken') for x in records]
            pools[key]={'rows':rows,'observed_at':r['fetched_at'].astimezone(UTC),'raw_id':r['id'],'hash':r['sha256']}
            evidence.append({'provider':provider,'date':day,'kind':kind,'rows':len(rows),'raw_id':r['id'],'hash':r['sha256'],'observed_at':r['fetched_at']})
        except (ValueError,KeyError,TypeError) as e:issues.append({'provider':provider,'date':day,'kind':kind,'error':str(e)})
    return [r['trade_date'] for r in calendar],pools,evidence,issues


def build_sentiment(output):
    calendar,pools,evidence,issues=event_snapshot()
    old_root=ROOT.parent/'research'
    with duckdb.connect() as db:
        missing_records=db.read_parquet(str(old_root/'missing_stock_dates.parquet')).project('stock_code,trade_date').fetchall()
    missing=defaultdict(set)
    for code,day in missing_records:missing[day].add(code)
    result=[];by_day={}
    for i,d in enumerate(calendar):
        previous=calendar[i-1] if i else None
        providers={p for p,day,k in pools if day==d}
        provider=next(iter(providers)) if len(providers)==1 else None
        up=pools.get((provider,d,'U'));broken=pools.get((provider,d,'Z'))
        previous_up=pools.get((provider,previous,'U'))
        previous_broken=pools.get((provider,previous,'Z'))
        prev_ok=previous_up and previous_broken and not ({r['stock_code'] for r in previous_up['rows']}&{r['stock_code'] for r in previous_broken['rows']})
        metrics=pool_metrics(up['rows'] if up else None,broken['rows'] if broken else None,
            previous_up['rows'] if prev_ok else None,missing[d],missing[previous])
        if len(providers)>1:metrics['event_quality']='multiple_sources_unreconciled'
        seen=max(up['observed_at'],broken['observed_at']) if up and broken else None
        row={'trade_date':d,**metrics,'event_provider':provider,
            'event_source_observed_at_utc':seen,'outcome_available_proxy_utc':datetime.combine(d,datetime.min.time(),SHANGHAI).replace(hour=16,minute=30).astimezone(UTC),
            'daily_missing_count':len(missing[d]),'industry_quality':'historical_effective_dated_mapping_unverified',
            'lag_limit_up_count':None,'lag_first_board_count':None,'lag_broken_limit_rate':None,'lag5_mean_up_count':None,
            'lag_source_observed_at_utc':None,'lag_event_provider':None}
        prev=by_day.get(previous)
        if prev and prev['event_quality']=='provider_pool_complete':
            row.update(lag_event_provider=prev['event_provider'],lag_limit_up_count=prev['limit_up_count'],lag_first_board_count=prev['first_board_count'],
                lag_broken_limit_rate=prev['broken_limit_rate'],lag_source_observed_at_utc=prev['event_source_observed_at_utc'])
        window=[by_day.get(x) for x in calendar[max(0,i-5):i]]
        if len(window)==5 and all(x and x['event_provider']==row['lag_event_provider'] and x['event_quality']=='provider_pool_complete' and x['limit_up_count'] is not None for x in window):
            row['lag5_mean_up_count']=sum(x['limit_up_count'] for x in window)/5
            row['lag_source_observed_at_utc']=max(x['event_source_observed_at_utc'] for x in window)
        by_day[d]=row
        if d>=date(2017,1,1):result.append(row)
    write_records(output/'sentiment.parquet',result)
    save_json(output/'event_evidence.json',{'snapshots':evidence,'issues':issues,'scope':{'tushare_proxy':'provider historical non-ST event scope','akshare':'provider observed event scope'},
        'historical_industry_mapping':'unverified; all technology metrics null','no_price_gap_imputation':True})
    return result,{'observed_days_by_provider':dict(Counter(r['event_provider'] for r in result if r['event_quality']=='provider_pool_complete')),
        'event_quality_counts':dict(Counter(r['event_quality'] for r in result)),
        'metric_available_days':{k:sum(r[k] is not None for r in result) for k in TARGETS},'source_issues':issues,
        'existing_gap_rows':len(missing_records),'post_march_2026_gap_rows':sum(d>=date(2026,3,16) for _,d in missing_records)}


def build():
    PROBES.mkdir(parents=True,exist_ok=True);ROOT.mkdir(parents=True,exist_ok=True)
    run=datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ');out=ROOT/'snapshots'/run;(out/'raw').mkdir(parents=True)
    qqq,schedule,qa=load_qqq(out);sentiment,sa=build_sentiment(out)
    bars={r['us_trade_date']:r for r in qqq};aligned=[]
    for row in sentiment:
        sig=align_session(row['trade_date'],schedule,bars,mode='retrospective')
        aligned.append({**row,'qqq_price_return':sig['price_return'],'qqq_cash_return_proxy':sig['cash_entitlement_return_proxy'],
            'qqq_us_trade_date':sig.get('us_trade_date'),'decision_cutoff_utc':sig['cutoff_utc'],
            'qqq_assumed_available_at_utc':sig.get('assumed_available_at_utc'),'qqq_signal_quality':sig['status'],
            'strict_qqq_status':align_session(row['trade_date'],schedule,bars,mode='strict')['status']})
    write_records(out/'aligned_diagnostics.parquet',aligned)
    audit={'qqq':qa,'ashare':sa,'strict_eligible_rows':sum(r['strict_qqq_status']=='observed_before_cutoff' for r in aligned),
        'alignment_leaks':sum(r['qqq_assumed_available_at_utc']>=r['decision_cutoff_utc'] for r in aligned if r['qqq_assumed_available_at_utc']),
        'nq_status':'unavailable_no_verified_free_preopen_history','root_scope':'independent module; A-share source files unchanged'}
    save_json(out/'audit.json',audit)
    manifest={'dataset':str(out.relative_to(ROOT)),'symbol':'QQQ','source':'nasdaq_public_historical',
        'ashare_start':'2017-01-01','ashare_end':str(END),'created_at_utc':datetime.now(UTC),
        'default_mode':'strict','price_adjustment':'raw provider OHLC; no cash adjustment; split completeness not issuer-certified',
        'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob('*.parquet')}}
    save_json(ROOT/'manifest.json',manifest)
    print(out)
    print(json.dumps({'qqq_rows':qa['target_rows'],'qqq_expected':qa['expected_sessions'],'qqq_missing':qa['missing_sessions'],
        'dividends':qa['dividends_in_target'],'ashare':sa,'strict_eligible':audit['strict_eligible_rows'],'alignment_leaks':audit['alignment_leaks']},default=str,ensure_ascii=False))


if __name__=='__main__':build()
