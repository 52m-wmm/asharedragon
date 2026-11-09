"""Additive daily partitions; immutable source revisions; no sparse daily overwrite."""
import json
from datetime import datetime
from uuid import uuid4
from sqlalchemy import select
from app.models import ProxyBatch, ProxyPartition, ProxyFact, Event, Raw, now
from app.collectors.ingest import save_raw, evidence, coverage
from app.providers.tushare_proxy import PROVIDER, ProxyError, digest
from app.providers.normalize import stock_code, day, number, integer, clock_time
from app.services.calendar import sessions

ENDPOINTS=('limit_list_d','kpl_list','limit_list_ths','daily_basic','stk_limit','stock_st','limit_cpt_list')


def normalize_limit(row, date, anomalies=None):
    if day(row['trade_date'])!=date:raise ValueError('source trade_date differs from request')
    kind={'U':'up','D':'down','Z':'broken'}.get(row.get('limit'))
    if not kind:raise ValueError('unknown limit type')
    code=stock_code(row['ts_code'])
    def optional(field,parser,minimum=None):
        original=row.get(field)
        try:
            value=parser(original)
            if value is not None and minimum is not None and value<minimum:
                raise ValueError('below valid minimum')
            return value
        except (ValueError,TypeError,OverflowError) as exc:
            if anomalies is not None:
                anomalies.append({'stock_code':code,'trade_date':str(date),'field':field,
                                  'raw_value':original,'reason':str(exc),'normalized_value':None})
            return None
    return dict(stock_code=code,stock_name=row.get('name'),industry=row.get('industry'),limit_type=kind,
                first_limit_time=optional('first_time',clock_time),last_limit_time=optional('last_time',clock_time),
                open_times=optional('open_times',integer,0),
                consecutive_limit_days=optional('limit_times',integer,1) if kind=='up' else None,
                is_sealed_at_close=kind=='up' if kind!='down' else None,
                data_source=PROVIDER,seal_amount=optional('fd_amount',number,0),amount=optional('amount',number,0),
                circulating_market_cap=optional('float_mv',number,0),total_market_cap=optional('total_mv',number,0),
                turnover_rate=optional('turnover_ratio',number,0),limit_price=None)


def normalize_aux(endpoint,row,date):
    if day(row['trade_date'])!=date:raise ValueError('source trade_date differs from request')
    key=str(row['ts_code'])
    if endpoint!='limit_cpt_list':key=stock_code(key)
    elif not key or len(key)>40:raise ValueError('invalid concept identifier')
    values={'stock_code' if endpoint!='limit_cpt_list' else 'concept_code':key,'trade_date':str(date),
            'data_kind':'provider_observed','source_fields':row}
    if endpoint=='daily_basic':
        for src,dst in [('total_mv','total_market_cap'),('circ_mv','circulating_market_cap')]:
            v=number(row.get(src));values[dst]=str(v*10000) if v is not None else None
        for field in ('pe_ttm','pb','turnover_rate'):
            v=number(row.get(field));values[field]=str(v) if v is not None else None
        values['money_unit']='CNY';values['source_money_unit']='10,000 CNY'
    elif endpoint in ('kpl_list','limit_list_ths'):
        # Raw numeric fields have different meanings/units across sources. Do not
        # guess that limit_order or free_float is the same as limit_list_d fd_amount.
        values.update(theme=row.get('theme'),limit_reason=row.get('lu_desc'),status=row.get('status'),
                      monetary_units='source_native_unverified')
        for src in ('lu_time','first_lu_time','last_lu_time','last_time'):
            t=clock_time(row.get(src));values[src]=t.isoformat() if t else None
        key+=':'+str(row.get('tag') if endpoint=='kpl_list' else row.get('limit_type'))
    elif endpoint=='stk_limit':
        for src in ('up_limit','down_limit'):
            v=number(row.get(src));values[src]=str(v) if v is not None else None
        values['price_unit']='CNY'
    return key,values


def complete_payload(client,endpoint,params,refresh=False,codes=None):
    """Reject capped responses; split only using documented filters, never fake pagination."""
    parts=[]
    def get(p):
        result=client.fetch(endpoint,refresh=refresh,**p);parts.append(result)
        rows=result['records'];cap=client.config[endpoint]['cap']
        if len(rows)<cap:return rows
        if endpoint=='limit_list_d' and 'limit_type' not in p:
            return [r for kind in ('U','D','Z') for r in get({**p,'limit_type':kind})]
        if endpoint=='limit_list_d' and 'exchange' not in p:
            return [r for exchange in ('SH','SZ','BJ') for r in get({**p,'exchange':exchange})]
        if endpoint=='limit_list_ths' and 'market' not in p:
            return [r for market in ('HS','GEM','STAR') for r in get({**p,'market':market})]
        if endpoint in ('daily_basic','stk_limit') and 'offset' not in p:
            # Verify proxy pagination empirically: overlapping/repeated keys are
            # rejected, not silently accepted as another page.
            combined=list(rows);seen={(r['ts_code'],r['trade_date']) for r in rows}
            for offset in range(len(rows),cap*20,cap):
                page=client.fetch(endpoint,refresh=refresh,**{**p,'offset':offset,'limit':cap});parts.append(page)
                keys={(r['ts_code'],r['trade_date']) for r in page['records']}
                if seen & keys:raise ProxyError('truncated','pagination ignored or overlapping pages')
                combined.extend(page['records']);seen.update(keys)
                if len(page['records'])<cap:return combined
            raise ProxyError('truncated','pagination safety bound exceeded')
        if codes and 'ts_code' not in p:
            selected=[c for c in codes if 'exchange' not in p or c.endswith('.'+p['exchange'])]
            return [r for code in selected for r in get({**p,'ts_code':code})]
        raise ProxyError('truncated','cap reached; explicit code scope required, partition not published')
    rows=get(params)
    # Validate every fetched split, including original capped responses.
    for part in parts:
        q=part['params']
        for r in part['records']:
            if day(r['trade_date'])!=day(params['trade_date']):raise ValueError('response date mismatch')
            if 'ts_code' in q and r['ts_code']!=q['ts_code']:raise ValueError('response code filter ignored')
            for field in ('limit_type','tag'):
                column='limit' if endpoint=='limit_list_d' and field=='limit_type' else field
                if field in q and r.get(column)!=q[field]:raise ValueError('response type filter ignored')
            if 'exchange' in q and not r['ts_code'].endswith('.'+q['exchange']):raise ValueError('response exchange filter ignored')
            if 'market' in q and r.get('market_type')!=q['market']:raise ValueError('response market filter ignored')
    if codes:rows=[r for r in rows if r['ts_code'] in codes]
    return {'source_id':client.source_id,'source_url':client.url,'columns':list(dict.fromkeys(k for r in rows for k in r)),
            'records':rows,'parts':parts,'fetched_at':max(p['fetched_at'] for p in parts),'mapping_version':'proxy-v0.3-cny-v1'}


def collect_partition(db,client,batch,endpoint,date,params,refresh=False,codes=None):
    identity={**params,'_codes':codes} if codes else params
    key=digest(identity)
    part=db.scalar(select(ProxyPartition).where(ProxyPartition.source_id==client.source_id,ProxyPartition.endpoint==endpoint,
                 ProxyPartition.trade_date==date,ProxyPartition.parameter_hash==key))
    if part and part.status=='success' and not refresh and not part.error_message:return part,0
    retry_partition=bool(part and (part.status!='success' or part.error_message))
    old_success=bool(part and part.status=='success')
    if not part:
        part=ProxyPartition(source_id=client.source_id,endpoint=endpoint,trade_date=date,parameters=identity,
                            parameter_hash=key,batch_id=batch.batch_id,status='running',audit={})
        db.add(part)
    part.attempts=(part.attempts or 0)+1;part.batch_id=batch.batch_id
    if not old_success:part.status='running'
    db.commit();part_id=part.id;payload={}
    try:
        payload=complete_payload(client,endpoint,params,refresh or retry_partition,codes)
        records=payload['records']
        validated_empty=(batch.parameters.get('validated_range') and payload['parts'] and all(p.get('range_validated') for p in payload['parts']))
        if not records and not validated_empty:raise ProxyError('empty_unverified','empty response is not proven zero events')
        unique={};duplicate=0;field_anomalies=[]
        for row in records:
            if endpoint=='limit_list_d':
                values=normalize_limit(row,date,field_anomalies);record_key=values['stock_code']+':'+values['limit_type']
            else:record_key,values=normalize_aux(endpoint,row,date)
            if record_key in unique:
                if unique[record_key][0]!=row:raise ValueError('conflicting duplicate source key')
                duplicate+=1;continue
            unique[record_key]=(row,values)
        if endpoint=='limit_list_d' and batch.parameters.get('safety_stop'):
            ups=[v for _,v in unique.values() if v['limit_type']=='up']
            critical=('first_limit_time','last_limit_time','open_times','consecutive_limit_days','seal_amount','circulating_market_cap')
            if ups and any(sum(v[k] is None for v in ups)/len(ups)>0.05 for k in critical):
                raise ProxyError('quality_decline','more than 5% missing in a core limit-up field')
        # source identity belongs in parameters so providers cannot share cache partitions.
        raw=save_raw(db,PROVIDER,endpoint,date,{'source_id':client.source_id,**identity},payload,'success')
        raw.fetched_at=datetime.fromisoformat(payload['fetched_at'])
        inserted=0
        model=Event if endpoint=='limit_list_d' else ProxyFact
        if not db.scalar(select(model.id).where(model.raw_id==raw.id).limit(1)):
            for record_key,(_,values) in unique.items():
                if endpoint=='limit_list_d':obj=Event(**values,**evidence(raw,date))
                else:obj=ProxyFact(record_key=record_key,normalized=values,**evidence(raw,date))
                db.add(obj);inserted+=1
        part=db.get(ProxyPartition,part_id)
        part.status='success';part.raw_id=raw.id;part.row_count=len(unique);part.error_message=None;part.updated_at=now()
        part.audit={'field_anomalies':field_anomalies,'field_anomaly_count':len(field_anomalies),'source_rows':len(records),'duplicates':duplicate,'normalized_rows':len(unique),'parts':len(payload['parts']),
                    'scope':'explicit_codes' if codes else 'provider_endpoint_universe',
                    'counts':{k:sum(v[1].get('limit_type')==k for v in unique.values()) for k in ('up','down','broken')} if endpoint=='limit_list_d' else {}}
        coverage(db,PROVIDER,endpoint+':'+client.source_id,date,'success',len(unique));db.commit()
        return part,inserted
    except Exception as exc:
        db.rollback();part=db.get(ProxyPartition,part_id)
        error=client.safe(str(exc))[:900]
        save_raw(db,PROVIDER,endpoint,date,{'source_id':client.source_id,**identity},payload,'failed',error)
        if not old_success:part.status='missing'
        part.error_message=error;part.updated_at=now()
        coverage(db,PROVIDER,endpoint+':'+client.source_id,date,'missing',error=error);db.commit()
        return part,0


def new_batch(db,client,start,end,endpoints,options=None):
    batch=ProxyBatch(batch_id=str(uuid4()),source_id=client.source_id,start_date=start,end_date=end,
                     endpoints=list(endpoints),parameters=options or {},status='pending',progress={})
    db.add(batch);db.commit();return batch


def run_batch(db,client,batch):
    dates=sessions(db,batch.start_date,batch.end_date)
    batch.status='running';batch.finished_at=None;db.commit()
    progress={};inserted=0
    try:
        for endpoint in batch.endpoints:
            items=[]
            for date in dates:
                variants=[{}]
                if endpoint=='kpl_list':variants=[{'tag':'涨停'},{'tag':'炸板'}]
                elif endpoint=='limit_list_ths':variants=[{'limit_type':'涨停池'},{'limit_type':'炸板池'}]
                for variant in variants:
                    params={'trade_date':date.strftime('%Y%m%d'),**variant}
                    codes=batch.parameters.get('codes')
                    if endpoint=='daily_basic' and codes:
                        # Slow endpoint: explicit single-stock requests, no implicit full market query.
                        queries=[({**params,'ts_code':c},[c]) for c in codes]
                    else:queries=[(params,codes)]
                    for q,scope in queries:
                        part,n=collect_partition(db,client,batch,endpoint,date,q,batch.parameters.get('refresh',False),scope)
                        inserted+=n;items.append({'partition_id':part.id,'date':str(date),'status':part.status,'raw_id':part.raw_id,
                                                 'rows':part.row_count,'error':part.error_message})
                        progress[endpoint]=items;batch.progress={**progress};db.commit()
                        print(json.dumps({'batch':batch.batch_id,'api':endpoint,'date':str(date),'status':part.status,'inserted':n}),flush=True)
                        if batch.parameters.get('safety_stop') and (part.status!='success' or part.error_message):
                            batch.status='paused';db.commit()
                            return {'batch_id':batch.batch_id,'status':'paused','inserted':inserted,'requests':client.used,
                                    'sessions':len(dates),'reason':part.error_message}

        all_items=[x for v in progress.values() for x in v]
        success=sum(x['status']=='success' and not x['error'] for x in all_items)
        batch.status='success' if all_items and success==len(all_items) else 'partial' if success else 'failed'
    except BaseException:
        db.rollback();batch.status='interrupted';db.commit();raise
    finally:
        batch.finished_at=now();db.commit()
    return {'batch_id':batch.batch_id,'status':batch.status,'inserted':inserted,'requests':client.used,'sessions':len(dates)}
