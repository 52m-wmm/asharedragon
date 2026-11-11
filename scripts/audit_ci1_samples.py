"""CI-1 offline field reconciliation; new audit artifacts only, no canonical publication.

Run from repo root: PYTHONPATH=.:backend .venv/bin/python scripts/audit_ci1_samples.py
All PostgreSQL reads run in one read-only repeatable-read transaction. No collectors.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import itertools
import json
from pathlib import Path
from zipfile import ZipFile
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from app.db.session import engine
from app.providers.normalize import daily_ak, stock_code, event_ak
from app.providers.baostock import normalize_daily
from app.collectors.tushare_history import normalize_limit
from app.services.data_inventory import POOL_SELECTION, RELEASES, raw_hash_method
from app.statistics.reference_recovery import recover_reference

RULE='ci1-local-field-reconciliation-v1'
ROOT=Path.home()/'Datasets/asharedragon'
DAYS=['2024-01-02','2026-03-13','2026-03-16','2026-11-06','2026-11-09','2026-11-10']
SOURCES=['purchased','akshare','baostock','tushare_proxy']
FIELDS=['open','high','low','close','pre_close','is_st','official_limit_up_price','official_limit_down_price']
PRICE_FIELDS={'open','high','low','close','pre_close','official_limit_up_price','official_limit_down_price'}
TOLERANCE=Decimal('0.000001')  # representation noise only, not a one-tick tolerance
PURCHASE='purchased_usb_0dfebf6ee9bc'
EXTENSION='akshare_em_unadjusted_20260316_20261106'


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
def file_hash(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def rows(cursor):return [dict(zip([x[0] for x in cursor.description],r)) for r in cursor.fetchall()]
def equivalent(a,b,field):
    if field in PRICE_FIELDS:return abs(Decimal(str(a))-Decimal(str(b)))<=TOLERANCE
    return a==b


def reconcile(candidates,field):
    """No vote, priority, median or implicit fill. Same-provider copies count once."""
    valid=[c for c in candidates if c.get('availability')=='available' and c.get('valid') and c.get('value') is not None]
    invalid=[c for c in candidates if c.get('availability')=='available' and not c.get('valid')]
    groups=defaultdict(list)
    for c in valid:groups[c['source']].append(c)
    conflicts=[(a,b) for a,b in itertools.combinations(valid,2) if not equivalent(a['value'],b['value'],field)]
    if conflicts:
        status='conflict';reason='incompatible_observed_values_no_majority_resolution'
    elif invalid:
        status='unknown';reason='present_but_quality_or_provenance_unresolved'
    elif len(groups)>=2:
        status='corroborated';reason='all_actual_comparable_source_values_agree'
    elif len(groups)==1:
        status='single_source_validated';reason='one_source_internal_checks_passed_no_independent_confirmation'
    else:
        status='unknown';reason='no_usable_comparable_field_evidence'
    return dict(status=status,reason=reason,independent_provider_family_count=len(groups),upstream_independence_verified=False,
        compared_sources=sorted(groups),selected_source=None,selected_value=None,
        selection_reason='CI-1 records evidence only; no canonical winner or active switch',
        consensus_value=valid[0]['value'] if status=='corroborated' else None,
        conflict_pairs=[{'left':a['observation_id'],'right':b['observation_id'],'left_value':a['value'],'right_value':b['value']} for a,b in conflicts])


class Audit:
    def __init__(self,root):
        self.root=root;self.files={};self.observations=[];self.pins={};self.raw_cache={}
        self.db=duckdb.connect(config={'threads':2,'memory_limit':'512MB'})
    def file(self,path):
        path=Path(path).resolve();key=str(path)
        if key not in self.files:self.files[key]=file_hash(path)
        return {'path':key,'sha256':self.files[key]}
    def read(self,path):
        self.file(path);return json.loads(Path(path).read_text())
    def pin(self):
        for key,rel in RELEASES.items():
            m=self.read(self.root/rel/'manifest.json');p=(self.root/rel/m['dataset']).resolve()
            summary=self.read(p/'summary.json')
            self.pins[key]={'snapshot_id':p.name,'path':str(p),'rule_version':summary.get('rule_version',summary.get('version',m.get('version'))),'manifest':self.file(self.root/rel/'manifest.json'),'summary':self.file(p/'summary.json')}
    def pg_raw(self,c,raw_id):
        if raw_id not in self.raw_cache:
            r=dict(c.execute(text('SELECT id,provider,endpoint,trade_date,parameters,raw_payload,sha256,ingestion_status,fetched_at FROM raw_observations WHERE id=:id'),{'id':raw_id}).mappings().one())
            r['hash_method']=raw_hash_method(r['parameters'],r['raw_payload'],r['ingestion_status'],r['sha256'])
            self.raw_cache[raw_id]=r
        return self.raw_cache[raw_id]
    def add(self,source,day,code,values,raw,proof,valid=True,reason=None,**extra):
        o=dict(id=f'{source}:{day}:{code}:{len(self.observations)}',source=source,trade_date=day,stock_code=code,
            values=values,raw_record=raw,raw_record_sha256=digest(raw),evidence=proof,valid=valid,quality_reason=reason,
            adjustment='none',price_unit='CNY',volume_unit='shares',rule_version=RULE,**extra)
        self.observations.append(o);return o

    def run(self,c):
        self.pin()
        gate=self.read(Path('data/daily_extension/sample_gate.json'))
        # Select by available evidence, not by observed agreement or by removing conflicts.
        pg=[dict(r) for r in c.execute(text("SELECT DISTINCT ON(provider,stock_code,trade_date,adjustment) * FROM stock_daily WHERE trade_date=ANY(:days) AND adjustment='none' ORDER BY provider,stock_code,trade_date,adjustment,fetched_at DESC,id DESC"),{'days':[datetime.fromisoformat(d).date() for d in DAYS]}).mappings()]
        recent=set(gate['stocks'])|{r['stock_code'] for r in pg if r['provider']=='baostock' and str(r['trade_date'])!='2024-01-02'}
        early={r['stock_code'] for r in pg if str(r['trade_date'])=='2024-01-02'}
        samples={day:sorted(early if day=='2024-01-02' else recent) for day in DAYS}
        full_counts=Counter((str(r['trade_date']),r['provider']) for r in pg)
        # Purchased daily files: verify selected raw ZIP members and processed round trip.
        stage=self.read(self.root/'staging/stocks/daily'/PURCHASE/'manifest.json')
        manifest=self.read(self.root/'processed/stocks/daily'/PURCHASE/'manifest.json')
        archive=Path(stage['archive']);archive_proof=self.file(archive)
        if archive_proof['sha256']!=stage['sha256']:raise ValueError('purchase_archive_hash_mismatch')
        with ZipFile(archive) as z:
            for day in DAYS:
                member='1d_price/'+day.replace('-','')+'.parquet'
                if member not in z.namelist():continue
                content=z.read(member);rawrows=pq.read_table(pa.BufferReader(content)).to_pylist()
                full_counts[day,'purchased']=len(rawrows)
                part=self.root/'processed/stocks/daily'/PURCHASE/manifest['months'][day[:7]]['file']
                part_proof=self.file(part)
                cleaned=rows(self.db.execute('SELECT * FROM read_parquet(?,hive_partitioning=false) WHERE trade_date=?::DATE',[str(part),day]))
                clean={r['stock_code']:r for r in cleaned}
                for i,r in enumerate(rawrows):
                    code=r['code']
                    if code not in samples[day]:continue
                    values={f:r.get(f) for f in FIELDS};valid=True;reasons=[]
                    if str(r['date'])!=day.replace('-',''):valid=False;reasons.append('member_record_date_mismatch')
                    prices=[r.get(f) for f in ['open','high','low','close']]
                    if any(v is None or v<=0 for v in prices) or r['high']<max(prices) or r['low']>min(prices):valid=False;reasons.append('invalid_ohlc')
                    for f in ['open','high','low','close','pre_close']:
                        if code not in clean or not equivalent(r[f],clean[code][f],f):valid=False;reasons.append('processed_roundtrip_mismatch:'+f)
                    self.add('purchased',day,code,values,r,{'archive':archive_proof,'member':member,'member_sha256':hashlib.sha256(content).hexdigest(),'row_index':i,'processed':part_proof,'processed_key':[code,day],'adjustment_evidence':self.file(self.root/'processed/stocks/daily'/PURCHASE/'manifest.json')},valid,';'.join(reasons) or None,reference_method='raw_pre_close',trade_status=None)
        # Raw AKShare checkpoints retain the ten-stock March overlap; same lineage as processed.
        em=self.read(self.root/'processed/stocks/daily'/EXTENSION/'manifest.json')
        extension_rows={}
        for day in DAYS:
            entry=em['months'].get(day[:7])
            if not entry:continue
            path=self.root/'processed/stocks/daily'/EXTENSION/entry['file']
            self.file(path)
            rr=rows(self.db.execute('SELECT * FROM read_parquet(?,hive_partitioning=false) WHERE trade_date=?::DATE',[str(path),day]))
            full_counts[day,'akshare_processed']=len(rr)
            extension_rows[day]={r['stock_code']:r for r in rr}
        for code in sorted(recent):
            path=self.root/'staging/stocks/daily'/EXTENSION/'symbols'/f'{code}.json'
            if not path.exists():continue
            saved=self.read(path)
            for i,r in enumerate(saved['raw']['records']):
                day=str(r['日期'])[:10]
                if day not in DAYS or code not in samples[day]:continue
                valid=True;reason=None
                try:n=daily_ak(r,code)
                except ValueError as exc:n={};valid=False;reason=str(exc)
                n['pre_close']=recover_reference(r)
                # Validate raw-to-processed OHLC only: processed pre_close is intentionally null.
                clean=extension_rows.get(day,{}).get(code)
                if clean and any(not equivalent(n.get(f),clean[f],f) for f in ['open','high','low','close']):valid=False;reason='processed_roundtrip_mismatch'
                self.add('akshare',day,code,{f:n.get(f) for f in FIELDS},r,{'file':self.file(path),'json_pointer':f'/raw/records/{i}','processed_present':clean is not None,'adjustment_evidence':self.file(self.root/'processed/stocks/daily'/EXTENSION/'manifest.json')},valid,reason,reference_method='raw_close_minus_change_pct_rounding_checked',trade_status=None)
        # Existing BaoStock file probes are raw evidence too, not just PG or U copies.
        for path in sorted(Path('data/baostock_probes').glob('*.json')):
            payload=json.loads(path.read_text())
            if not isinstance(payload,dict):continue
            relevant=[(i,r) for i,r in enumerate(payload.get('records',[])) if r.get('date') in DAYS and 'adjustflag' in r]
            if not relevant:continue
            self.file(path)
            for day in DAYS:
                full_counts[day,'baostock_file_versions']+=sum(r['date']==day for _,r in relevant)
            for i,r in relevant:
                day=r['date']
                try:code=stock_code(r['code'])
                except ValueError:continue
                if code not in samples[day]:continue
                valid=True;reason=None
                try:n=normalize_daily(r)
                except ValueError as exc:n={};valid=False;reason=str(exc)
                self.add('baostock',day,code,{f:n.get(f) for f in FIELDS},r,
                    {'file':self.file(path),'json_pointer':f'/records/{i}','provider_version':payload.get('version'),'fetched_at':payload.get('fetched_at'),'adjustment_raw':r.get('adjustflag'),'same_provider_as_PG':True},valid,reason,reference_method='raw_preclose',trade_status=n.get('trade_status'))
        # Daily PostgreSQL observations: retain source rows, compare normalized round trip.
        for row in pg:
            day=str(row['trade_date']);code=row['stock_code'];source=row['provider']
            if code not in samples[day] or source not in ('akshare','baostock'):continue
            raw=self.pg_raw(c,row['raw_id']);matches=[]
            for i,r in enumerate(raw['raw_payload'].get('records',[])):
                d=str(r.get('date',r.get('日期','')))[:10]
                if d!=day:continue
                try:rc=stock_code(r.get('code',r.get('股票代码',code)))
                except ValueError:continue
                if rc==code:matches.append((i,r))
            if len(matches)!=1:
                self.add(source,day,code,{f:None for f in FIELDS},{},{'raw_id':raw['id'],'row_id':row['id']},False,'raw_record_locator_not_unique');continue
            i,r=matches[0];valid=raw['hash_method']!='unmatched' and raw['ingestion_status']=='success';reason=None
            try:n=normalize_daily(r) if source=='baostock' else daily_ak(r,code)
            except ValueError as exc:n={};valid=False;reason=str(exc)
            if source=='akshare' and raw['parameters'].get('adjust','')!='':valid=False;reason='adjustment_not_unadjusted'
            differences=[]
            for f in ['open','high','low','close','pre_close','is_st']:
                if row.get(f) is not None and n.get(f) is not None and not equivalent(row[f],n[f],f):differences.append(f)
            if differences:valid=False;reason='normalized_roundtrip_mismatch:'+','.join(differences)
            proof={'raw_id':raw['id'],'row_id':row['id'],'endpoint':raw['endpoint'],'raw_sha256':raw['sha256'],'hash_method':raw['hash_method'],'json_pointer':f'/records/{i}','fetched_at':str(raw['fetched_at']),'adjustment_request':{k:v for k,v in raw['parameters'].items() if k in ('adjust','adjustflag','frequency')}}
            self.add(source,day,code,{f:n.get(f) for f in FIELDS},r,proof,valid,reason,reference_method='raw_preclose' if source=='baostock' else 'raw_close_minus_change_pct_rounding_checked',trade_status=n.get('trade_status'),stored_pre_close=row.get('pre_close'))
        results=[]
        for day,codes in samples.items():
            for code in codes:
                obs=[o for o in self.observations if o['trade_date']==day and o['stock_code']==code]
                for field in FIELDS:
                    candidates=[]
                    for source in SOURCES:
                        found=[o for o in obs if o['source']==source]
                        if not found:
                            candidates.append(dict(source=source,observation_id=None,value=None,availability='not_available',valid=False,reason='no_daily_record_in_selected_local_sources'));continue
                        for o in found:
                            v=o['values'].get(field);valid=o['valid'];reason=o['quality_reason']
                            if field in PRICE_FIELDS and v is not None and (not Decimal(str(v)).is_finite() or Decimal(str(v))<=0):valid=False;reason='nonpositive_or_nonfinite_price'
                            if field in PRICE_FIELDS and o.get('trade_status') is False:valid=False;reason='suspended_not_usable_for_return_or_trading_price'
                            candidates.append(dict(source=source,observation_id=o['id'],value=v,availability='available' if v is not None else 'not_available',valid=valid,reason=reason if v is not None else ('historical_ST_identity_not_supplied' if field=='is_st' else 'official_limit_price_not_supplied' if field.startswith('official_limit_') else 'reference_missing_or_recovery_failed' if field=='pre_close' else 'price_field_not_supplied'),reference_method=o.get('reference_method') if field=='pre_close' else None))
                    results.append(dict(id=f'daily:{day}:{code}:{field}',kind='daily',trade_date=day,stock_code=code,field=field,candidates=candidates,rule_version=RULE,unit='CNY' if field in PRICE_FIELDS else 'boolean',snapshot_context={k:v['snapshot_id'] for k,v in self.pins.items()},**reconcile(candidates,field)))
        return samples,full_counts,results

    def events(self,c):
        days=['2020-01-02',*DAYS];results=[];coverage=[]
        archives=[]
        for path in sorted(Path('data/raw/tushare/limit_list_d/2020/01').glob('20200102_*.json')):
            a=self.read(path)
            if a.get('payload'):
                archives.append((path,a))
        for source in ('akshare','tushare_proxy'):
            selected=[dict(r) for r in c.execute(text('WITH selected AS ('+POOL_SELECTION+') SELECT * FROM selected WHERE trade_date=ANY(:days) ORDER BY trade_date,limit_type,stock_code'),{'p':source,'days':[datetime.fromisoformat(d).date() for d in days]}).mappings()]
            for day in days:
                for kind in ('up','down','broken'):
                    batch=[r for r in selected if str(r['trade_date'])==day and r['limit_type']==kind]
                    # Full 2020 three pools (107 rows); at most three deterministic recent rows per pool.
                    sample=batch if day=='2020-01-02' else batch[:3]
                    coverage.append(dict(source=source,trade_date=day,category=kind,available_rows=len(batch) if batch else None,availability='available' if batch else 'not_available',sample_rows=len(sample),raw_ids=sorted({r['raw_id'] for r in batch})))
                    for row in sample:
                        raw=self.pg_raw(c,row['raw_id']);code=row['stock_code'];matches=[]
                        for i,r in enumerate(raw['raw_payload'].get('records',[])):
                            try:rc=stock_code(r.get('ts_code',r.get('代码')))
                            except (ValueError,TypeError):continue
                            if rc==code:matches.append((i,r))
                        if len(matches)!=1:raise ValueError('event_raw_locator_not_unique')
                        i,r=matches[0];anomalies=[]
                        n=normalize_limit(r,datetime.fromisoformat(day).date(),anomalies) if source=='tushare_proxy' else event_ak(r,kind)
                        proof={'raw_id':raw['id'],'row_id':row['id'],'endpoint':raw['endpoint'],'raw_sha256':raw['sha256'],'hash_method':raw['hash_method'],'json_pointer':f'/records/{i}','fetched_at':str(raw['fetched_at']),'request_date':str(raw['trade_date']),'request_parameters':{k:v for k,v in raw['parameters'].items() if k in ('date','trade_date','limit_type')}}
                        if source=='tushare_proxy':
                            proof['archive_matches']=[]
                            for path,a in archives:
                                records=a['payload'].get('records',[])
                                for j,rr in enumerate(records):
                                    if digest(rr)==digest(r):proof['archive_matches'].append({'file':self.file(path),'json_pointer':f'/payload/records/{j}','payload_hash_matches':digest(a['payload'])==a.get('response_hash'),'same_provider_not_independent':True})
                        values=dict(event_date=day,event_code=code,event_category=n['limit_type'],consecutive_limit_days=n['consecutive_limit_days'],official_limit_up_price=None,official_limit_down_price=None)
                        o=self.add(source,day,code,values,r,proof,raw['hash_method']!='unmatched',None,event_category=kind,field_anomalies=anomalies,kind='event')
                        for field,value in values.items():
                            roundtrip=True
                            if field=='consecutive_limit_days':roundtrip=value==row.get(field)
                            if field=='event_category':
                                expected={'stock_zt_pool_em':'up','stock_zt_pool_dtgc_em':'down','stock_zt_pool_zbgc_em':'broken'}.get(raw['endpoint']) if source=='akshare' else {'U':'up','D':'down','Z':'broken'}.get(raw['parameters'].get('limit_type'))
                                roundtrip=value==row['limit_type'] and value==expected
                            if field=='event_date':
                                requested=raw['parameters'].get('date',raw['parameters'].get('trade_date',str(raw['trade_date'])))
                                roundtrip=str(raw['trade_date'])==day and str(requested).replace('-','')==day.replace('-','')
                                if source=='tushare_proxy':roundtrip=roundtrip and str(r['trade_date'])==day.replace('-','')
                            archive_ok=source!='tushare_proxy' or any(x['payload_hash_matches'] for x in proof['archive_matches'])
                            cand=dict(source=source,observation_id=o['id'],value=value,availability='available' if value is not None else 'not_available',valid=o['valid'] and roundtrip and archive_ok,reason=('normalization_mismatch' if not roundtrip else 'official_limit_price_not_supplied_event_close_not_substitute' if field.startswith('official_limit_') else 'not_applicable_outside_up_pool' if field=='consecutive_limit_days' and kind!='up' else 'supplier_height_missing_or_invalid' if value is None else 'supplier_event_only_not_strict_limit_or_ST_qualification'))
                            others=[dict(source=s,observation_id=None,value=None,availability='not_available',valid=False,reason='no_independent_comparable_event_record') for s in SOURCES if s!=source]
                            results.append(dict(id=f'event:{source}:{day}:{kind}:{code}:{field}',kind='event',trade_date=day,stock_code=code,event_category=kind,field=field,candidates=[cand,*others],rule_version=RULE,source_scope='supplier_event_diagnostic_only',snapshot_context={k:v['snapshot_id'] for k,v in self.pins.items()},**reconcile([cand,*others],field)))
        return results,coverage

    def frozen_rows(self,samples):
        result=[]
        for day,codes in samples.items():
            year,month,_=day.split('-')
            part=Path(self.pins['U']['path'])/'stock_daily'/f'year={year}'/f'month={int(month)}'/'data_0.parquet'
            self.file(part)
            selected=rows(self.db.execute('SELECT stock_code,trade_date,stock_day_id,open,high,low,close,pre_close,price_source,pg_daily_id,supplement_raw_id,is_st,trade_status,price_quality,st_quality,status_source,historical_status_evidence_ids,price_conflict,status_conflict,event_conflict,official_limit_up_price,official_limit_down_price FROM read_parquet(?) WHERE trade_date=?::DATE AND stock_code IN (SELECT unnest(?))',[str(part),day,codes]))
            present={r['stock_code']:r for r in selected}
            for code in codes:
                result.append(dict(trade_date=day,stock_code=code,snapshot_id=self.pins['U']['snapshot_id'],location=str(part),record_key=[code,day],row=present.get(code),availability='available' if code in present else 'not_available',role='existing frozen output, never an independent corroborating provider'))
        return result

    def boundaries(self,samples,results):
        output=[]
        for prev,day in [('2026-03-13','2026-03-16'),('2026-11-06','2026-11-09'),('2026-11-09','2026-11-10')]:
            path=Path(self.pins['D1']['path'])/(day[:7]+'.json')
            d=next(r for r in self.read(path) if r['trade_date']==day)
            old=d['matched_cohort_breadth']
            paired=[]
            for code in sorted(set(samples[prev])&set(samples[day])):
                a=next(r for r in results if r['kind']=='daily' and r['trade_date']==prev and r['stock_code']==code and r['field']=='close')
                b=next(r for r in results if r['kind']=='daily' and r['trade_date']==day and r['stock_code']==code and r['field']=='pre_close')
                paired.append(dict(stock_code=code,previous_close_evidence=a['id'],current_reference_evidence=b['id'],previous_close_status=a['status'],current_reference_status=b['status'],caution='No equality assumption between previous close and current pre_close; corporate actions/suspension may intervene'))
            output.append(dict(previous_trading_day=prev,trade_date=day,existing_D1={k:old.get(k) for k in ['calculable','direction','reason_codes','common_count','rule_version','snapshot_id']},source_changed_count=len(old.get('source_changed_codes',[])),same_scope_cross_source_gate='unknown',promotion_allowed=False,reason='small_purposive_sample_does_not_establish_full_cohort_source_reference_or_suspension_compatibility',sample_pairs=paired,evidence=self.file(path)))
        return output


def status_counts(records):
    counts=Counter(r['status'] for r in records)
    return {s:counts[s] for s in ['corroborated','single_source_validated','conflict','unknown']}


def summaries(samples,counts,observations,results):
    daily=[r for r in results if r['kind']=='daily'];coverage=[];pairs=[]
    for day,codes in samples.items():
        for source in SOURCES:
            obs=[o for o in observations if o['trade_date']==day and o['source']==source and o.get('kind')!='event']
            found={o['stock_code'] for o in obs}
            for field in FIELDS:
                comparable={o['stock_code'] for o in obs if o['valid'] and o['values'].get(field) is not None and not (field in PRICE_FIELDS and o.get('trade_status') is False)}
                coverage.append(dict(trade_date=day,source=source,field=field,sampled_stock_days=len(codes),sample_record_count=len(found),sample_record_ratio=len(found)/len(codes),usable_field_count=len(comparable),not_available_records=len(codes)-len(found),local_pg_records=counts.get((day,source)) if source not in ('purchased','tushare_proxy') else None,local_file_record_versions=counts.get((day,'baostock_file_versions')) if source=='baostock' else None,local_purchased_records=counts.get((day,'purchased')) if source=='purchased' else None,local_processed_records=counts.get((day,'akshare_processed')) if source=='akshare' else None,coverage_scope='selected_local_files_and_latest_PG; ratios are sample coverage, never whole market'))
        for s1,s2 in itertools.combinations(SOURCES,2):
            for field in FIELDS:
                comparable=agree=disagree=overlap=union=0
                for r in [x for x in daily if x['trade_date']==day and x['field']==field]:
                    aa=[c for c in r['candidates'] if c['source']==s1 and c['observation_id']];bb=[c for c in r['candidates'] if c['source']==s2 and c['observation_id']]
                    union+=bool(aa or bb);overlap+=bool(aa and bb)
                    a=[c for c in aa if c['valid'] and c['value'] is not None];b=[c for c in bb if c['valid'] and c['value'] is not None]
                    if not a or not b:continue
                    comparable+=1
                    if all(equivalent(x['value'],y['value'],field) for x in a for y in b):agree+=1
                    else:disagree+=1
                pairs.append(dict(trade_date=day,left=s1,right=s2,field=field,record_overlap=overlap,record_union=union,overlap_ratio=overlap/union if union else None,comparable_stock_days=comparable,agreement_count=agree,conflict_count=disagree,agreement_ratio=agree/comparable if comparable else None,denominator_definition='field comparable stock-days; absence is not disagreement'))
    return dict(source_field_coverage=coverage,pairwise_field_overlap=pairs,
        status_counts=status_counts(results),
        by_kind={kind:status_counts(r for r in results if r['kind']==kind) for kind in ['daily','event']},
        by_field={f:status_counts(r for r in results if r['field']==f) for f in sorted({r['field'] for r in results})},
        reasons=dict(Counter(r['reason'] for r in results)),candidate_missing_or_quality_reasons=dict(Counter(c.get('reason') for r in results for c in r['candidates'] if c.get('reason') and (not c.get('valid') or c.get('availability')=='not_available'))))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=ROOT);parser.add_argument('--output',type=Path);args=parser.parse_args()
    output=args.output or Path('data/ci1_audit')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output.mkdir(parents=True,exist_ok=False)  # never overwrite a prior run or an existing snapshot
    audit=Audit(args.root)
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        assert c.execute(text('SHOW transaction_read_only')).scalar()=='on'
        samples,counts,results=audit.run(c)
        events,event_coverage=audit.events(c);results+=events
        c.rollback()
    boundaries=audit.boundaries(samples,results)
    frozen_rows=audit.frozen_rows(samples)
    summary=summaries(samples,counts,audit.observations,results)
    summary.update(event_coverage=event_coverage,sample_selection=samples,sample_policy='10 existing AK overlap gate stocks plus all locally present BaoStock on target days; early date all existing PG sample stocks; no filtering by agreement',event_sample_policy='all 107 proxy events on 2020-01-02; first three codes of each available recent AK category; absent categories unknown',source_independence='Different provider families only; original upstream independence of purchased vendor/BaoStock/AKShare not established; corroborated means observed cross-provider agreement, not exchange certification')
    def save(name,value):
        p=output/name
        if name.endswith('.jsonl'):p.write_text(''.join(json.dumps(r,ensure_ascii=False,sort_keys=True,default=str)+'\n' for r in value))
        else:p.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,default=str)+'\n')
    save('observations.jsonl',audit.observations);save('field_reconciliation.jsonl',results)
    save('coverage_and_agreement.json',summary)
    save('conflicts.json',{'policy':'No voting or winner selection; preserve original values and unresolved quality conditions','field_conflicts':[r for r in results if r['status']=='conflict'],'quality_unknown':[r for r in results if r['reason']=='present_but_quality_or_provenance_unresolved']})
    save('boundaries.json',boundaries)
    save('existing_U_rows.jsonl',frozen_rows)
    changed=[p for p,h in audit.files.items() if file_hash(p)!=h]
    if changed:raise ValueError(f'Inputs changed during audit: {changed}')
    save('manifest.json',dict(rule_version=RULE,spec='v2.2 section 17',generated_at=datetime.now(timezone.utc).isoformat(),snapshots=audit.pins,input_files=audit.files,input_files_unchanged=True,read_only_transaction=True,price_comparison_absolute_tolerance_CNY=str(TOLERANCE),source_count_policy='provider families; same raw/archive/processed copies never independent',output_mode='audit_evidence_only_not_canonical',active_switch=False,code_sha256={p:file_hash(p) for p in ['scripts/audit_ci1_samples.py','backend/app/providers/normalize.py','backend/app/providers/baostock.py','backend/app/collectors/tushare_history.py','backend/app/statistics/reference_recovery.py','backend/app/services/data_inventory.py']},decimal_serialization='Decimal values preserved as JSON strings; missing values are null, boolean false is valid ST evidence',official_limit_policy='No limit formula or event close substitution; only explicit official-limit fields can supply this evidence',outputs={p.name:file_hash(p) for p in output.iterdir() if p.is_file()}))
    audit.db.close()
    print(json.dumps({'output':str(output.resolve()),'sample_stock_days':sum(map(len,samples.values())),'observations':len(audit.observations),'field_judgments':len(results),'status_counts':summary['status_counts'],'by_kind':summary['by_kind']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
