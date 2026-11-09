"""Validated unfiltered ranges; immutable source payloads remain reprocessable."""
from collections import Counter
from app.providers.tushare_proxy import ProxyError,digest
from app.collectors.tushare_history import normalize_limit,complete_payload

NEAR_CAP=2250

def validate(payload,dates):
    allowed={d.strftime('%Y%m%d'):d for d in dates};seen=set();counts=Counter()
    for row in payload['records']:
        if row.get('trade_date') not in allowed:raise ProxyError('range_filter','date outside requested trading sessions')
        normalize_limit(row,allowed[row['trade_date']])
        key=(row['trade_date'],row['ts_code'],row['limit'])
        if key in seen:raise ProxyError('duplicate','duplicate range key')
        seen.add(key);counts[row['trade_date']]+=1
    return set(allowed)-set(counts)

def fetch_ranges(client,dates):
    """Split near-cap or incomplete ranges; never publish a capped parent."""
    recovery=getattr(client,'recovery_payload',None)
    wanted={d.strftime('%Y%m%d') for d in dates}
    if isinstance(recovery,dict) and wanted.issubset({r['trade_date'] for r in recovery['records']}):
        payload={**recovery,'records':[r for r in recovery['records'] if r['trade_date'] in wanted],
                 'projection':'offline_recovery_from_validated_range'}
    else:
        payload=client.fetch('limit_list_d',start_date=dates[0].strftime('%Y%m%d'),end_date=dates[-1].strftime('%Y%m%d'))
    missing=validate(payload,dates)
    if len(payload['records'])>=NEAR_CAP or missing:
        if len(dates)==1:
            if missing:raise ProxyError('empty_unverified','single session empty; original response retained')
            # Extreme market days can exceed even the single-day cap. Reuse
            # verified type/exchange partitioning rather than silently truncate.
            old_cap=client.config['limit_list_d']['cap']
            client.config['limit_list_d']['cap']=NEAR_CAP
            try:merged=complete_payload(client,'limit_list_d',{'trade_date':dates[0].strftime('%Y%m%d')})
            finally:client.config['limit_list_d']['cap']=old_cap
            if validate(merged,dates):raise ProxyError('empty_unverified','split day absent')
            merged.update(params={'start_date':dates[0].strftime('%Y%m%d'),'end_date':dates[0].strftime('%Y%m%d')},
                          response={'split_responses':merged['parts']},attempts=[a for p in merged['parts'] for a in p['attempts']])
            merged['archive_path']=client.save('limit_list_d',merged['params'],merged)
            yield dates,merged;return
        middle=len(dates)//2
        yield from fetch_ranges(client,dates[:middle]);yield from fetch_ranges(client,dates[middle:]);return
    yield dates,payload

class RangeView:
    """Offline daily/category projection; explicitly not a second HTTP response."""
    def __init__(self,client,payload):
        self.client=client;self.payload=payload
        self.source_id,self.url,self.config=client.source_id,client.url,client.config
    def safe(self,value):return self.client.safe(value)
    def fetch(self,endpoint,refresh=False,**params):
        if endpoint!='limit_list_d' or 'trade_date' not in params:raise ValueError('invalid range view')
        rows=[r for r in self.payload['records'] if r['trade_date']==params['trade_date'] and ('limit_type' not in params or r['limit']==params['limit_type'])]
        return {'source_id':self.source_id,'source_url':self.url,'params':params,'records':rows,'columns':self.payload['columns'],
                'fetched_at':self.payload['fetched_at'],'attempts':[],
                'range_validated':True,'projection':'offline_from_unfiltered_range',
                'source_request':self.payload['params'],'source_archive':self.payload['archive_path'],
                'source_response_hash':digest(self.payload['response'])}
