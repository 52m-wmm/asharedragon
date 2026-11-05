"""Small, resumable BaoStock monthly batches; every symbol is an independent transaction."""
import hashlib
from collections import defaultdict
from datetime import date, timedelta
from uuid import uuid4
from sqlalchemy import select, func
from app.models import (Raw, Daily, Stock, Job, TradingDay, ReconstructionRun,
                        ReconstructionFact, ReconstructionDay, ValidationReport, now)
from app.collectors.ingest import save_raw, coverage, evidence
from app.providers.baostock import FIELDS, normalize_daily, from_vendor, to_vendor
from app.providers.client import Client
from app.providers.normalize import day
from app.services.calendar import sessions, completed_date
from app.services.queries import latest_daily, latest_events, serialize
from app.analytics.reconstruction import classify, streaks, board_for, compare, VERSION, UNKNOWN


def cached_raw(db, endpoint, params, client, trade_date=None, job_id=None, force=False):
    if not force:
        stmt = select(Raw).where(Raw.provider=='baostock', Raw.endpoint==endpoint,
                                 Raw.ingestion_status=='success')
        for key, value in params.items():
            stmt = stmt.where(Raw.parameters[key].as_string()==str(value))
        for raw in db.scalars(stmt.order_by(Raw.id.desc())):
            if raw.parameters == params:
                return raw
    try:
        payload = client.fetch(endpoint, **params)
        if not payload.get('records'):
            raise ValueError('BaoStock empty response: coverage unverified')
        raw = save_raw(db, 'baostock', endpoint, trade_date, params, payload, 'success', job_id=job_id)
        db.commit()
        return raw
    except Exception as exc:
        db.rollback()
        raw = save_raw(db, 'baostock', endpoint, trade_date, params, {}, 'failed', str(exc)[:900], job_id)
        db.commit()
        raise


def basic_map(raw):
    if not {'code','ipoDate','outDate','type','status'}.issubset(raw.raw_payload['columns']):
        raise ValueError('BaoStock basic schema changed')
    result = {}
    for row in raw.raw_payload['records']:
        if row['type'] != '1': continue
        try: code = from_vendor(row['code'])
        except ValueError: continue  # explicitly exclude indices/B shares
        value = dict(row, stock_code=code, listing_date=day(row['ipoDate']) if row.get('ipoDate') else None,
                     delisting_date=day(row['outDate']) if row.get('outDate') else None)
        if code in result and result[code] != value: raise ValueError('Conflicting basic records')
        result[code] = value
    if not result: raise ValueError('No verified A-share basic records')
    return result


def store_basics(db, raw):
    basics = basic_map(raw)
    if not db.scalar(select(Stock.id).where(Stock.raw_id==raw.id).limit(1)):
        observed_date = raw.fetched_at.date()
        for code, item in basics.items():
            db.add(Stock(stock_code=code, stock_name=item['code_name'], exchange=code[-2:],
                         board=board_for(code), listing_date=item['listing_date'], delisting_date=item['delisting_date'],
                         status=item['status'], valid_from=observed_date, valid_to=observed_date,
                         **evidence(raw, observed_date)))
        db.commit()
    return basics


def choose_sample(basics, size):
    """Deterministic stratification, no future return or event labels in sampling."""
    groups = defaultdict(list)
    for code, item in basics.items():
        group = 'delisted' if item['delisting_date'] else (board_for(code) or 'unknown_board')
        groups[group].append(code)
    for group in groups:
        groups[group].sort(key=lambda code: hashlib.sha256(code.encode()).hexdigest())
    result = []
    while len(result) < size and any(groups.values()):
        for group in sorted(groups):
            if groups[group] and len(result) < size: result.append(groups[group].pop(0))
    return result


def ingest_symbol(db, code, start, end, client, job_id=None, force=False):
    params = {'code':to_vendor(code),'fields':FIELDS,'start_date':str(start),'end_date':str(end),
              'frequency':'d','adjustflag':'3'}
    raw = cached_raw(db, 'query_history_k_data_plus', params, client, end, job_id, force)
    try:
        rows = [normalize_daily(r) for r in raw.raw_payload['records']]
        if any(r['stock_code']!=code or not start<=r['trade_date']<=end for r in rows):
            raise ValueError('Returned code/date outside request')
        if len({r['trade_date'] for r in rows}) != len(rows): raise ValueError('Duplicate daily dates')
        expected_dates=set(sessions(db,start,end))
        if any(r['trade_date'] not in expected_dates for r in rows): raise ValueError('Daily row falls outside verified exchange sessions')
        inserted = 0
        if not db.scalar(select(Daily.id).where(Daily.raw_id==raw.id).limit(1)):
            for row in rows:
                d = row.pop('trade_date')
                db.add(Daily(**row, **evidence(raw,d))); inserted += 1
        present = {day(r['date']) for r in raw.raw_payload['records']}
        for d in sessions(db, start, end):
            coverage(db,'baostock',f'daily:{code}',d,'success' if d in present else 'missing',
                     1 if d in present else None, None if d in present else 'No row; lifecycle or source gap not inferred')
        db.commit()
        return len(rows), inserted, raw.id
    except Exception:
        db.rollback()
        raise


def active(item, d):
    return item['listing_date'] is not None and item['listing_date']<=d and (item['delisting_date'] is None or item['delisting_date']>=d)


def run_month(db, job, client=None):
    """Called by the existing single-worker queue, or the guarded history CLI."""
    client = client or Client('baostock', timeout=180, interval=.5)
    start, end = job.start_date, job.end_date
    if (start.year,start.month)!=(end.year,end.month): raise ValueError('One history job must fit one month')
    dates = sessions(db, start, end)
    if not dates: raise ValueError('No trading sessions in requested interval')
    job.status='running'; db.commit()
    raw = cached_raw(db,'query_stock_basic',{},client,completed_date(),job.job_id,job.parameters.get('refresh_basics',False))
    basics = store_basics(db,raw)
    universe = {c:r for c,r in basics.items() if r['listing_date'] and r['listing_date']<=end and
                (not r['delisting_date'] or r['delisting_date']>=start)}
    # Keep point-in-time roster evidence; do not treat today's surviving list as historical membership.
    roster_ids=[]
    for d in sorted({dates[0], dates[-1]}):
        roster = cached_raw(db,'query_all_stock',{'day':str(d)},client,d,job.job_id)
        roster_ids.append(roster.id)
        non_stocks={r['code'] for r in raw.raw_payload['records'] if r['type']!='1'}
        for r in roster.raw_payload['records']:
            if r['code'] in non_stocks: continue
            try: c=from_vendor(r['code'])
            except ValueError: continue
            if c not in basics:
                raise ValueError(f'Historical roster {c} absent from basic catalogue; cannot claim complete lifecycle coverage')
            if c not in universe: universe[c]=basics[c]
    requested_codes=job.parameters.get('codes') or job.parameters.get('selected_codes')
    if requested_codes:
        codes = sorted(set(requested_codes))
        unavailable=set(codes)-set(universe)
        if unavailable: raise ValueError('Codes not in historical provider universe: '+','.join(sorted(unavailable)))
    elif job.parameters.get('all_stocks'):
        codes = sorted(universe)
    else:
        codes = choose_sample(universe,job.parameters.get('sample_size',24))
    warmup = start-timedelta(days=job.parameters.get('warmup_days',60))
    # Enough exchange-calendar history is retained to classify IPO sessions and month boundaries.
    sessions(db,warmup,end)
    job.parameters={**job.parameters,'selected_codes':codes,'universe_size':len(universe),'warmup_start':str(warmup)}
    db.commit()
    errors=[]; source_ids={raw.id,*roster_ids}
    if job.parameters.get('market_transport'):
        if not job.parameters.get('all_stocks'): raise ValueError('Market transport requires explicit all_stocks')
        for d in sessions(db,warmup,end):
            try:
                received,inserted,raw_id=ingest_market_day(db,d,client,job.job_id,job.parameters.get('force',False))
                job.rows_received+=received;job.rows_inserted+=inserted;source_ids.add(raw_id);db.commit()
            except Exception as exc:
                db.rollback();job=db.get(Job,job.job_id);errors.append(f'{d}: {exc}')
                coverage(db,'baostock','market_daily',d,'missing',error=str(exc)[:700]);db.commit()
            print(f'{job.job_id} market {d} inserted={job.rows_inserted}',flush=True)
    else:
        for code in codes:
            item=universe[code]
            first=max(warmup,item['listing_date'])
            last=min(end,item['delisting_date']) if item['delisting_date'] else end
            try:
                received,inserted,raw_id=ingest_symbol(db,code,first,last,client,job.job_id,job.parameters.get('force',False))
                job.rows_received+=received;job.rows_inserted+=inserted;source_ids.add(raw_id);db.commit()
            except Exception as exc:
                db.rollback(); job=db.get(Job,job.job_id)
                errors.append(f'{code}: {exc}')
                coverage(db,'baostock',f'daily:{code}',end,'missing',error=str(exc)[:700]);db.commit()
            print(f'{job.job_id} {code} {len(source_ids)} sources / {len(codes)} selected',flush=True)
    run = reconstruct(db,job,codes,universe,warmup,source_ids)
    validate_run(db,run)
    job.status=('partial' if job.rows_received else 'failed') if errors else 'success'
    job.error_message='\n'.join(errors)[-6000:] or None
    job.finished_at=now();db.commit()
    return job


def reconstruct(db,job,codes,universe,warmup,source_ids):
    calendar=list(db.scalars(select(TradingDay.trade_date).where(TradingDay.exchange=='SSE').order_by(TradingDay.trade_date)))
    positions={d:i for i,d in enumerate(calendar)}
    window=[d for d in calendar if warmup<=d<=job.end_date]
    from pathlib import Path
    from app.analytics import reconstruction as algorithm
    source_ids.update(db.scalars(select(TradingDay.raw_id).distinct()))
    run_parameters={**job.parameters,'algorithm_sha256':hashlib.sha256(Path(algorithm.__file__).read_bytes()).hexdigest()}
    run=ReconstructionRun(run_id=str(uuid4()),job_id=job.job_id,start_date=job.start_date,end_date=job.end_date,
        algorithm_version=VERSION,scope='provider_universe' if set(codes)==set(universe) else 'sample',
        parameters=run_parameters,source_raw_ids=sorted(source_ids),status='building')
    db.add(run);db.flush()
    all_facts=[]; per_date=defaultdict(list)
    daily_by_code=defaultdict(dict)
    for record in db.scalars(select(Daily).where(Daily.provider=='baostock',Daily.stock_code.in_(codes),Daily.trade_date.between(warmup,job.end_date),Daily.adjustment=='none').order_by(Daily.id)):
        daily_by_code[record.stock_code][record.trade_date]=record
    for code in codes:
        item=universe[code]
        daily=daily_by_code[code]
        source_ids.update(r.raw_id for r in daily.values())
        rows=[];prev=None
        for d in window:
            if not active(item,d): continue
            record=daily.get(d); normalized=serialize(record) if record else None
            fact=dict(stock_code=code,trade_date=d,daily_id=record.id if record else None,
                      **classify(normalized,item['listing_date'],item['delisting_date'],positions,prev))
            rows.append(fact);prev=normalized
        streaks(rows)
        for fact in rows:
            per_date[fact['trade_date']].append(fact)
            if fact['trade_date']>=job.start_date:
                db.add(ReconstructionFact(run_id=run.run_id,**fact));all_facts.append(fact)
    for d in window:
        if d<job.start_date: continue
        facts=per_date[d]; prior=per_date.get(calendar[positions[d]-1],[]) if positions[d] else []
        expected=sum(active(r,d) for r in universe.values())
        received=sum(f['daily_id'] is not None for f in facts)
        resolved=sum(f['classification'] not in UNKNOWN for f in facts)
        ups=[f for f in facts if f['classification']=='up']
        known=[f['consecutive_limit_days'] for f in ups if f['consecutive_limit_days'] is not None]
        rates={}
        today={f['stock_code']:f for f in facts}
        for n,name in [(1,'second'),(2,'third')]:
            # Same selected cohort, including yesterday's unresolved heights in the quality gate.
            cohort=[f for f in prior if f['classification']=='up' and f['consecutive_limit_days']==n]
            reliable=bool(prior) and all(f['classification'] not in UNKNOWN and
                         (f['classification']!='up' or f['consecutive_limit_days'] is not None) for f in prior)
            reliable=reliable and all(c['stock_code'] in today and today[c['stock_code']]['classification'] not in UNKNOWN for c in cohort)
            rates[f'{name}_board_promotion_rate']=(sum(today[c['stock_code']]['consecutive_limit_days']==n+1 for c in cohort)/len(cohort)) if reliable and cohort else None
        quality='provider_universe_classified' if resolved==expected and len(facts)==expected else 'partial_sample_or_unclassified'
        db.add(ReconstructionDay(run_id=run.run_id,trade_date=d,expected_stocks=expected,selected_stocks=len(facts),
            received_stocks=received,classified_stocks=resolved,unknown_stocks=len(facts)-resolved,
            coverage_rate=received/expected if expected else None,classification_rate=resolved/expected if expected else None,
            limit_up_count=len(ups) if resolved else None,max_consecutive_boards=max(known,default=0) if resolved and len(known)==len(ups) else None,
            quality=quality,**rates))
    run.source_raw_ids=sorted(source_ids)
    run.status='ready';db.commit();return run


def validate_run(db,run):
    for d in sessions(db,run.start_date,run.end_date):
        ref=latest_events(db,'akshare',d,d)
        observed=[e for e in ref if e.limit_type=='up']
        if not observed: continue  # never publish metrics against an unverified empty pool
        facts=[serialize(f) for f in db.scalars(select(ReconstructionFact).where(ReconstructionFact.run_id==run.run_id,ReconstructionFact.trade_date==d))]
        result=compare(facts,[e.stock_code for e in observed])
        reference_raw=db.get(Raw,observed[0].raw_id)
        ref_prices={str(r['代码']):r.get('最新价') for r in reference_raw.raw_payload.get('records',[]) if '代码' in r}
        by_code={f['stock_code']:f for f in facts}
        for difference in result['false_positives']+result['false_negatives']:
            fact=by_code[difference['stock_code']]
            source=db.get(Daily,fact['daily_id']) if fact['daily_id'] else None
            difference['evidence']={'daily_id':fact['daily_id'],'rule_price':str(fact['limit_price']) if fact['limit_price'] else None,
                'bao_close':str(source.close) if source else None,'bao_pre_close':str(source.pre_close) if source else None,
                'historical_is_st':source.is_st if source else None,'ak_pool_latest_price':ref_prices.get(difference['stock_code'][:6])}
            reference_close=ref_prices.get(difference['stock_code'][:6])
            if source and reference_close is not None and float(source.close)!=float(reference_close):
                difference['reason']='closing_price_disagreement_between_sources'
            elif reference_close is not None:
                difference['reason']='same_close_but_rule_or_reference_price_disagreement_unresolved'
            elif source and source.is_st:
                difference['reason']='possible_ST_scope_difference_unverified'
        result['quality_gate']='comparison_only_not_ground_truth_certification'

        heights={f['stock_code']:f['consecutive_limit_days'] for f in facts if f['classification']=='up'}
        result['height_mismatches']=[{'stock_code':e.stock_code,'observed':e.consecutive_limit_days,'reconstructed':heights[e.stock_code],
                                     'reason':'streak_boundary_gap_or_provider_definition_unresolved'} for e in observed
                                    if e.stock_code in heights and heights[e.stock_code]!=e.consecutive_limit_days]
        existing=db.scalar(select(ValidationReport).where(ValidationReport.run_id==run.run_id,ValidationReport.trade_date==d,
                                                         ValidationReport.reference_raw_id==observed[0].raw_id))
        if not existing:db.add(ValidationReport(run_id=run.run_id,trade_date=d,reference_raw_id=observed[0].raw_id,result=result))
    db.commit()


def ingest_market_day(db, d, client, job_id=None, force=False):
    """Verified free v0.9.4 endpoint, one unadjusted provider-market snapshot per day."""
    raw=cached_raw(db,'query_daily_history_k_AStock',{'date':str(d)},client,d,job_id,force)
    records=[]
    try:
        for row in raw.raw_payload['records']:
            # This endpoint is documented by the installed SDK as A stocks, nevertheless validate.
            try: code=from_vendor(row['code'])
            except ValueError: continue
            if board_for(code) is None: continue
            records.append(normalize_daily(row))
        if not records or any(r['trade_date']!=d for r in records):raise ValueError('Market snapshot date/schema mismatch')
        if len({r['stock_code'] for r in records})!=len(records):raise ValueError('Duplicate market daily stock codes')
        inserted=0
        if not db.scalar(select(Daily.id).where(Daily.raw_id==raw.id).limit(1)):
            for row in records:
                row.pop('trade_date');db.add(Daily(**row,**evidence(raw,d)));inserted+=1
        coverage(db,'baostock','market_daily',d,'success',len(records));db.commit()
        return len(records),inserted,raw.id
    except Exception:
        db.rollback();raise
