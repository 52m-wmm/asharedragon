"""D2 local root-cause census + bounded reference repairs + all-stock boundary audit.

Only writes a new content-addressed experimental snapshot; never updates a manifest pointer.
PYTHONPATH=.:backend .venv/bin/python scripts/build_reference_repair.py
"""
import argparse
from collections import Counter,defaultdict
from datetime import date
import hashlib,json,shutil,tempfile
from pathlib import Path
from zipfile import ZipFile
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from app.db.session import engine
from app.providers.normalize import daily_ak,stock_code
from app.providers.baostock import normalize_daily
from app.services.data_inventory import raw_hash_method,RELEASES
from app.analytics.reference_repair import VERSION,PRICES,equal,reference_check,matches,bridge,repair_reference,confirmed_cohort,ratio_delta_pp
from app.analytics.matched_cohort_breadth import POLICY,matched_breadth
from app.statistics.market_breadth import daily_sql,validate_daily
from scripts.build_market_breadth import sha,rows,calculate,snapshot
from scripts.build_direction_signals import open_profiles

ROOT=Path.home()/'Datasets/asharedragon'
PURCHASE='purchased_usb_0dfebf6ee9bc';EXTENSION='akshare_em_unadjusted_20260316_20261106'
PAIRS=[('2026-03-13','2026-03-16'),('2026-11-06','2026-11-09'),('2026-11-09','2026-11-10')]
BOUND_DAYS=sorted({d for pair in PAIRS for d in pair})

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,default=str,allow_nan=False,separators=(',',':'))
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def dump(path,value):path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str,allow_nan=False,indent=2)+'\n')
def linefile(path,values):path.write_text(''.join(canonical(r)+'\n' for r in values))


class Build:
    def __init__(self,root):self.root=root;self.files={};self.pins={};self.evidence=[];self.by_key=defaultdict(list);self.pg_hashes={}
    def track(self,path):
        p=Path(path).resolve()
        if str(p) not in self.files:self.files[str(p)]=sha(p)
        return p
    def read(self,path):return json.loads(self.track(path).read_text())
    def initialize(self):
        for key,rel in RELEASES.items():
            base=self.root/rel;m=self.read(base/'manifest.json');path=(base/m['dataset']).resolve()
            if not path.is_relative_to(base.resolve()):raise ValueError('Parent path escape')
            self.pins[key]={'snapshot_id':path.name,'path':str(path),'manifest_sha256':self.files[str((base/'manifest.json').resolve())]}
            for f in sorted(path.rglob('*')):
                if f.is_file():self.track(f)
        self.u=Path(self.pins['U']['path']);self.e1=Path(self.pins['E1']['path']);self.h=Path(self.pins['H']['path'])
        bi=self.read(self.e1/'inputs.json')
        if Path(bi['source_snapshot'])!=self.u or Path(bi['history_snapshot'])!=self.h:raise ValueError('Parent version mismatch')
        for name,h in bi['code_sha256'].items():
            if sha(name)!=h:raise ValueError('Frozen E1 implementation changed')
        for name,h in self.read(self.root/'research/unified/manifest.json')['files'].items():
            if sha(self.u/name)!=h:raise ValueError('Frozen U hash mismatch')
        self.lifecycle=self.read(bi['lifecycle_report'])
        self.db=open_profiles(self.u,self.h/'recovered_references.parquet',self.lifecycle)
        self.pm=self.read(self.root/'processed/stocks/daily'/PURCHASE/'manifest.json')
        self.em=self.read(self.root/'processed/stocks/daily'/EXTENSION/'manifest.json')
        for source,manifest in [(PURCHASE,self.pm),(EXTENSION,self.em)]:
            if manifest.get('adjustment_type')!='none' or manifest.get('amount_unit')!='CNY':raise ValueError('Price contract incompatible')
            for entry in manifest['months'].values():
                p=self.track(self.root/'processed/stocks/daily'/source/entry['file'])
                if entry.get('sha256') and sha(p)!=entry['sha256']:raise ValueError('Processed partition hash mismatch')
        stage=self.read(self.root/'staging/stocks/daily'/PURCHASE/'manifest.json')
        self.archive=self.track(stage['archive'])
        if sha(self.archive)!=stage['sha256']:raise ValueError('Original ZIP checksum mismatch')

    def census(self):
        unknown=[];boundary=[]
        for q in sorted(self.e1.glob('*.quality.json')):
            month=q.name[:7];bounds=rows(self.db,"SELECT min(trade_date) a,max(trade_date) b FROM cal WHERE strftime(trade_date,'%Y-%m')=?",[month])[0]
            calculate(self.db,bounds['a'],bounds['b'])
            unknown+=rows(self.db,"SELECT stock_code,trade_date,open,high,low,close,pre_close,prior_close,prior_price_verified,price_source,reference_basis,price_reason,trade_status,lifecycle_status,identity_status FROM evaluated WHERE price_reason='reference_unverified'")
            if month in ('2026-03','2026-11'):
                boundary+=rows(self.db,"SELECT stock_code,trade_date,open,high,low,close,pre_close,reference_price,reference_basis,daily_return,price_source,price_reason,price_conflict,identity_status,trade_status,lifecycle_status FROM evaluated WHERE in_scope AND trade_date IN (SELECT unnest(?)::DATE)",[BOUND_DAYS])
            if month.endswith('-12'):print('census',month,len(unknown),flush=True)
        if len(unknown)!=41456:raise ValueError('Reference-unverified baseline changed; do not silently reuse counts')
        self.db.register('unknown_rows',pa.Table.from_pylist(unknown))
        self.db.read_parquet(str(self.root/'processed/stocks/daily'/PURCHASE/'year=*/month=*/*.parquet'),hive_partitioning=False).create_view('purchased')
        self.db.execute('CREATE TEMP VIEW frozen_stocks AS SELECT * FROM frozen.stocks')
        causes=rows(self.db,"""SELECT q.*,c.prev previous_session,s.listing_date,a.adj_factor current_factor,b.adj_factor previous_factor,
          CASE WHEN q.trade_date=DATE '2017-01-03' THEN 'research_start_prior_outside_U' WHEN q.prior_close IS NULL THEN 'previous_stock_record_missing'
          WHEN NOT coalesce(q.prior_price_verified,false) THEN 'previous_price_untrusted' WHEN abs(q.pre_close-q.prior_close)>1e-8 THEN 'supplied_reference_differs_previous_close' ELSE 'other' END root_cause,
          CASE WHEN q.prior_close IS NULL AND s.listing_date>c.prev AND s.listing_date<=q.trade_date THEN 'catalogue_listing_window_unverified' WHEN q.prior_close IS NULL THEN 'prior_record_gap_unexplained' ELSE 'prior_record_present' END gap_context,
          CASE WHEN a.adj_factor IS NULL OR b.adj_factor IS NULL THEN 'factor_context_missing' WHEN abs(a.adj_factor-b.adj_factor)>1e-8 THEN 'factor_changed_not_independent_proof' ELSE 'factor_unchanged' END factor_context
          FROM unknown_rows q JOIN cal c USING(trade_date) LEFT JOIN purchased a ON a.stock_code=q.stock_code AND a.trade_date=q.trade_date
          LEFT JOIN purchased b ON b.stock_code=q.stock_code AND b.trade_date=c.prev LEFT JOIN frozen_stocks s ON s.stock_code=q.stock_code ORDER BY q.trade_date,q.stock_code""")
        # Deterministic stratified evidence sample: no picking by whether a repair succeeds.
        groups=Counter();selected={}
        known={('000001.SZ','2017-07-21'),('000001.SZ','2018-07-12'),('688290.SH','2024-12-04'),('300750.SZ','2023-04-26'),('002594.SZ','2025-07-29'),('600036.SH','2026-01-16')}
        for r in causes:
            key=(r['stock_code'],str(r['trade_date']));group=(str(r['trade_date'])[:4],r['root_cause'],r['factor_context'])
            if groups[group]<5 or key in known:selected[key]=r
            groups[group]+=1
        self.causes=causes;self.boundary=boundary;self.selected=selected
        self.wanted=set(selected)|{(r['stock_code'],str(r['trade_date'])) for r in boundary}
        self.prior_keys={(r['stock_code'],str(r['previous_session'])) for r in selected.values() if r['root_cause']=='research_start_prior_outside_U'}

    def add(self,provider,code,day,raw,proof,origin,normal=None):
        ref,reason=reference_check(raw,provider) if provider in ('purchased','akshare','baostock') else (raw.get('pre_close'),'saved_document_example_not_live_API')
        valid=True
        try:
            if normal is not None:values=normal
            elif provider=='akshare':values=daily_ak(raw,code)
            elif provider=='baostock':values=normalize_daily(raw)
            else:values=raw
            prices={k:float(values[k]) for k in PRICES}
            if any(v<=0 for v in prices.values()) or prices['high']<max(prices.values()) or prices['low']>min(prices.values()):valid=False
        except (ValueError,KeyError,TypeError):prices={k:None for k in PRICES};valid=False
        valid=valid and ref is not None and values.get('trade_status') is not False
        e=dict(id=f'{provider}:{day}:{code}:{origin}',provider=provider,stock_code=code,trade_date=day,**prices,
               reference_price=ref,valid=valid,reference_check=reason,adjustment='none',price_unit='CNY',
               source_refs=proof,raw_record=raw,raw_record_sha256=digest(raw),rule_version=VERSION)
        self.evidence.append(e);self.by_key[code,day].append(e);return e

    def raw_sources(self):
        raw_previous={};wanted_with_prior=self.wanted|self.prior_keys
        with ZipFile(self.archive) as z:
            names=set(z.namelist())
            for day in sorted({d for _,d in wanted_with_prior}):
                member='1d_price/'+day.replace('-','')+'.parquet'
                if member not in names:continue
                content=z.read(member);items=pq.read_table(pa.BufferReader(content)).to_pylist();member_hash=hashlib.sha256(content).hexdigest()
                for i,r in enumerate(items):
                    key=(r['code'],day)
                    if key not in wanted_with_prior:continue
                    if str(r['date'])!=day.replace('-',''):raise ValueError('ZIP record date mismatch')
                    proof={'archive':str(self.archive),'archive_sha256':self.files[str(self.archive)],'member':member,'member_sha256':member_hash,'row_index':i,'price_source':PURCHASE,'pct_unit':'fraction_not_percent'}
                    e=self.add('purchased',r['code'],day,r,proof,'zip')
                    if key in self.prior_keys:raw_previous[key]=e
        self.raw_previous=raw_previous
        codes={c for c,_ in self.wanted}
        cache=self.root/'staging/stocks/daily'/EXTENSION/'symbols'
        for path in sorted(cache.glob('*.json')):
            if path.stem not in codes:continue
            saved=self.read(path)
            for i,r in enumerate(saved['raw']['records']):
                day=str(r['日期'])[:10];code=saved['code']
                if (code,day) not in self.wanted:continue
                if stock_code(str(r.get('股票代码',code)).zfill(6))!=code:raise ValueError('AK cached symbol mismatch')
                self.add('akshare',code,day,r,{'file':str(path),'sha256':self.files[str(path.resolve())],'json_pointer':f'/raw/records/{i}','price_source':EXTENSION,'adjustment_evidence':str(self.root/'processed/stocks/daily'/EXTENSION/'manifest.json'),'pct_unit':'percent'},'file')
        frozen=rows(self.db,"SELECT DISTINCT ON(provider,stock_code,trade_date,adjustment) * FROM pg_daily WHERE adjustment='none' AND ingestion_status='success' ORDER BY provider,stock_code,trade_date,adjustment,fetched_at DESC,id DESC")
        chosen=[r for r in frozen if (r['stock_code'],str(r['trade_date'])) in self.wanted]
        ids=sorted({r['raw_id'] for r in chosen})
        with engine.connect() as c:
            c.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            rawrows={r['id']:dict(r) for r in c.execute(text('SELECT id,provider,endpoint,parameters,raw_payload,sha256,ingestion_status,fetched_at FROM raw_observations WHERE id=ANY(:ids)'),{'ids':ids}).mappings()}
            c.rollback()
        indexed={}
        for ident,r in rawrows.items():
            method=raw_hash_method(r['parameters'],r['raw_payload'],r['ingestion_status'],r['sha256'])
            if method=='unmatched' or r['ingestion_status']!='success':raise ValueError('Raw integrity/status mismatch')
            self.pg_hashes[str(ident)]={'sha256':r['sha256'],'hash_method':method,'provider':r['provider'],'endpoint':r['endpoint']}
            index=defaultdict(list)
            for i,x in enumerate(r['raw_payload'].get('records',[])):
                try:code=stock_code(x.get('code',str(x.get('股票代码',r['parameters'].get('symbol',''))).zfill(6)))
                except ValueError:continue
                day=str(x.get('date',x.get('日期','')))[:10];index[code,day].append((i,x))
            indexed[ident]=index
        for r in chosen:
            code=r['stock_code'];day=str(r['trade_date']);raw=rawrows[r['raw_id']]
            if raw['provider']!=r['provider'] or raw['endpoint']!=r['endpoint']:raise ValueError('Raw identity mismatch')
            found=indexed[r['raw_id']][code,day]
            if len(found)!=1:raise ValueError('Raw stock/date locator not unique')
            if r['provider']=='akshare' and raw['parameters'].get('adjust','')!='':raise ValueError('Adjusted raw incompatible')
            i,x=found[0]
            self.add(r['provider'],code,day,x,{'raw_id':raw['id'],'pg_daily_id':r['id'],'raw_sha256':raw['sha256'],'hash_method':self.pg_hashes[str(raw['id'])]['hash_method'],'endpoint':raw['endpoint'],'json_pointer':f'/records/{i}','U_frozen_pg_table':str(self.u/'pg_stock_daily.parquet')},'pg:'+str(r['id']))
        # Reuse saved file probes/document examples, never make new provider requests.
        for path in sorted(Path('data/baostock_probes').glob('*.json')):
            payload=json.loads(path.read_text())
            if not isinstance(payload,dict):continue
            relevant=[]
            for i,r in enumerate(payload.get('records',[])):
                if 'adjustflag' not in r:continue
                try:code=stock_code(r['code'])
                except ValueError:continue
                if (code,r['date']) in self.wanted:relevant.append((i,r,code))
            if not relevant:continue
            self.track(path)
            for i,r,code in relevant:self.add('baostock',code,r['date'],r,{'file':str(path.resolve()),'sha256':self.files[str(path.resolve())],'json_pointer':f'/records/{i}'},path.name)
        doc=Path('data/price_adjustment_audit/tushare_official_example.json');payload=self.read(doc)
        for i,r in enumerate(payload['records']):
            day=r['trade_date'];day=day[:4]+'-'+day[4:6]+'-'+day[6:]
            if (r['ts_code'],day) in self.wanted:self.add('tushare_saved_document_example',r['ts_code'],day,r,{'file':str(doc.resolve()),'sha256':self.files[str(doc.resolve())],'json_pointer':f'/records/{i}','source_kind':payload['source_kind']},'saved_doc')
        self.evidence.sort(key=lambda e:e['id'])

    def primary(self,row):
        source=row['price_source'];key=(row['stock_code'],str(row['trade_date']))
        def selected(e):
            if source==PURCHASE:return e['provider']=='purchased'
            if source==EXTENSION:return e['source_refs'].get('price_source')==EXTENSION
            return e['provider']==source and 'raw_id' in e['source_refs']
        found=sorted([e for e in self.by_key[key] if selected(e)],key=lambda e:e['id'])
        return found[0] if found else None

    def historical_repairs(self):
        repairs=[]
        for key,row in sorted(self.selected.items(),key=lambda x:(x[0][1],x[0][0])):
            primary=self.primary(row);alt=[e for e in self.by_key[key] if e['provider']!='purchased']
            prior=self.raw_previous.get((row['stock_code'],str(row['previous_session'])))
            decision=repair_reference(row,primary,alt,prior['close'] if prior and prior['valid'] else None)
            repairs.append({**row,**decision,'reference_price':row['pre_close'] if decision['status']=='repaired' else None,
                            'primary_evidence':primary['id'] if primary else None,'alternative_evidence':[e['id'] for e in alt],
                            'previous_archive_evidence':prior['id'] if prior else None,'allowed_uses':['observable_sample_breadth'] if decision['status']=='repaired' else [],
                            'blocked_uses':['strict_non_ST_limits','official_limit_prices','point_in_time_backtest'],'rule_version':VERSION})
        return repairs

    def boundary_audit(self):
        profiles={}
        for r in self.boundary:
            day=str(r['trade_date']);p=profiles.setdefault(day,dict(trade_date=day,source_snapshot=self.u.name,breadth_rule_version='observed-market-breadth-v1',scope=POLICY['scope'],stocks={}))
            p['stocks'][r['stock_code']]=r
        details=[];comparisons=[]
        for prev,day in PAIRS:
            a=profiles[prev];b=profiles[day];proofs={}
            for code in sorted(a['stocks'].keys()|b['stocks'].keys()):
                x=a['stocks'].get(code);y=b['stocks'].get(code)
                common=bool(x and y and x['price_reason']==y['price_reason']=='valid')
                ep=self.primary(x) if x else None;ec=self.primary(y) if y else None
                if common:
                    proof=bridge(x,y,ep,ec,self.by_key[code,prev],self.by_key[code,day]);proofs[code]=proof
                else:proof={'status':'not_in_original_valid_common_cohort','reason':'original_price_missing_suspended_or_not_observed_retained'}
                details.append(dict(trade_date=day,previous_trading_day=prev,stock_code=code,in_original_common=common,previous=x,current=y,
                    previous_evidence=ep['id'] if ep else None,current_evidence=ec['id'] if ec else None,
                    previous_alternatives=[e['id'] for e in self.by_key[code,prev]],current_alternatives=[e['id'] for e in self.by_key[code,day]],**proof,rule_version=VERSION))
            d1=next(r for r in self.read(Path(self.pins['D1']['path'])/(day[:7]+'.json')) if r['trade_date']==day)['matched_cohort_breadth']
            reproduced=matched_breadth(day,prev,a,b,snapshot_id=d1['snapshot_id'],input_snapshots=d1['input_snapshots'])
            if reproduced['common_codes_sha256']!=d1['common_codes_sha256'] or reproduced['reason_codes']!=d1['reason_codes']:raise ValueError('D1 baseline reproduction mismatch')
            new=confirmed_cohort(d1,a,b,proofs)
            comparisons.append(dict(trade_date=day,previous_trading_day=prev,old=d1,new=new,per_stock_status=dict(Counter(p['status'] for p in proofs.values())),per_stock_reason=dict(Counter(p['reason'] for p in proofs.values()))))
        return details,comparisons

    def aggregate_repairs(self,repairs):
        eligible=[r for r in repairs if r['status']=='repaired'];days={str(r['trade_date']) for r in eligible};diff=[]
        self.db.register('patches',pa.Table.from_pylist([dict(stock_code=r['stock_code'],trade_date=r['trade_date'],patched_reference=r['reference_price'],patched_basis='D2:'+r['reason']) for r in eligible],schema=pa.schema([('stock_code',pa.string()),('trade_date',pa.date32()),('patched_reference',pa.float64()),('patched_basis',pa.string())])))
        for month in sorted({d[:7] for d in days}):
            bounds=rows(self.db,"SELECT min(trade_date) a,max(trade_date) b FROM cal WHERE strftime(trade_date,'%Y-%m')=?",[month])[0]
            old,_=calculate(self.db,bounds['a'],bounds['b']);self.db.execute('CREATE OR REPLACE TEMP TABLE old_evaluated AS SELECT * FROM evaluated')
            self.db.execute("""CREATE OR REPLACE TEMP TABLE evaluated AS SELECT d.* REPLACE(
             CASE WHEN p.stock_code IS NOT NULL THEN 'valid' ELSE d.price_reason END AS price_reason,
             CASE WHEN p.stock_code IS NOT NULL THEN p.patched_basis ELSE d.reference_basis END AS reference_basis,
             CASE WHEN p.stock_code IS NOT NULL THEN CAST(d.close AS DECIMAL(20,2))/CAST(p.patched_reference AS DECIMAL(20,2))-1 ELSE d.daily_return END AS daily_return,
             CASE WHEN p.stock_code IS NOT NULL THEN CASE WHEN d.close>p.patched_reference THEN 'up' WHEN d.close<p.patched_reference THEN 'down' ELSE 'flat' END ELSE d.direction END AS direction)
             FROM old_evaluated d LEFT JOIN patches p ON d.stock_code=p.stock_code AND d.trade_date=p.trade_date AND d.price_reason='reference_unverified' AND abs(d.reference_price-p.patched_reference)<1e-8""")
            new=rows(self.db,daily_sql());validate_daily(new)
            for x,y in zip(old,new):
                if str(x['trade_date']) in days:
                    if x['observed_count']!=y['observed_count'] or x['st_unknown_count']!=y['st_unknown_count']:raise ValueError('Denominator or identity changed')
                    diff.append(dict(trade_date=x['trade_date'],old=x,new=y,price_valid_delta=y['price_valid_count']-x['price_valid_count'],advancing_ratio_delta_pp=ratio_delta_pp(x['advancing_ratio'],y['advancing_ratio'])))
        if sum(r['price_valid_delta'] for r in diff)!=len(eligible):raise ValueError('Patch not applied once per stock/day')
        return diff


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=ROOT);args=parser.parse_args();b=Build(args.root.resolve())
    b.initialize();b.census();print('root causes complete; sample',len(b.selected),flush=True);b.raw_sources();print('raw evidence',len(b.evidence),flush=True)
    repairs=b.historical_repairs();details,boundaries=b.boundary_audit();aggregate=b.aggregate_repairs(repairs)
    code={p:sha(p) for p in ['scripts/build_reference_repair.py','backend/app/analytics/reference_repair.py','scripts/build_market_breadth.py','scripts/build_direction_signals.py','backend/app/statistics/market_breadth.py','backend/app/statistics/reference_recovery.py','backend/app/analytics/matched_cohort_breadth.py','backend/app/providers/normalize.py','backend/app/providers/baostock.py','backend/app/services/data_inventory.py']}
    inputs=dict(spec='v2.4 section 19',spec_edited_at='2026-11-10T13:37:03.210Z',rule_version=VERSION,code_commit=None,parents=b.pins,files=b.files,pg_raw_sha256=b.pg_hashes,code_sha256=code,
                runtime={'pyarrow':pa.__version__,'duckdb':duckdb.__version__},sample_policy='first 5 by date/code within year/root-cause/factor-context plus 6 saved corporate-action examples; no success-based sampling',
                boundary_policy='all observed stock-days; never remove an unresolved common member; same-provider unadjusted raw contract OR same-day overlap OR native current reference equals verified prior raw close',
                field_policy='same-response arithmetic is single-source validation, not corporate-action/official-limit/ST confirmation; factor never authorizes repair')
    key=digest(inputs)[:20];base=args.root/'statistics/reference_repair/snapshots';base.mkdir(parents=True,exist_ok=True);target=base/key
    fixed={ (r['stock_code'],str(r['trade_date'])):r for r in repairs}
    eligible=sum(r['status']=='repaired' for r in repairs);oldsum=b.read(b.e1/'summary.json')['totals']
    oldd1=b.read(Path(b.pins['D1']['path'])/'summary.json')['totals'];new_boundary_count=sum(x['new']['calculable'] for x in boundaries)
    summary=dict(rule_version=VERSION,snapshot_id=key,mode='D2_offline_repair_experiment_not_Canonical',root_cause_counts=dict(Counter(r['root_cause'] for r in b.causes)),
        root_cause_by_year={y:dict(Counter(r['root_cause'] for r in b.causes if str(r['trade_date']).startswith(y))) for y in sorted({str(r['trade_date'])[:4] for r in b.causes})},
        factor_context=dict(Counter(r['factor_context'] for r in b.causes)),missing_previous_context=dict(Counter(r['gap_context'] for r in b.causes if r['root_cause']=='previous_stock_record_missing')),
        sample_count=len(repairs),sample_status=dict(Counter(r['status'] for r in repairs)),repair_field_status=dict(Counter(r.get('field_status','unknown') for r in repairs)),
        old_reference_unverified=41456,new_reference_unverified=41456-eligible,old_price_unknown=oldsum['price_unknown_count'],new_price_unknown=oldsum['price_unknown_count']-eligible,
        old_price_valid=oldsum['price_valid_count'],new_price_valid=oldsum['price_valid_count']+eligible,observed_count=oldsum['observed_count'],
        boundary_rows=len(details),boundary_experiment_old_uncalculable_days=oldd1['unknown'],boundary_experiment_new_uncalculable_days=oldd1['unknown']-new_boundary_count,
        boundary_newly_calculable=new_boundary_count,boundary_results=[{'trade_date':x['trade_date'],'common_count':x['new']['common_count'],'calculable':x['new']['calculable'],'direction':x['new']['direction'],'delta':x['new']['delta'],'unresolved':len(x['new']['unresolved_common_codes']),'reasons':x['per_stock_reason']} for x in boundaries],
        separation='Historical reference repairs affect only displayed E1 date aggregates; boundary experiment uses unchanged original E1 prices. No all-history direction recomputation or active switch.',
        lifecycle_counts=b.lifecycle['counts'],coverage_ratio=None,strict_non_ST_eligible=None,official_limit_prices=None,active_switch=False)
    with tempfile.TemporaryDirectory(prefix='.d2-build-',dir=base) as tmp:
        out=Path(tmp);dump(out/'inputs.json',inputs);dump(out/'summary.json',summary)
        for r in b.causes:
            decision=fixed.get((r['stock_code'],str(r['trade_date'])))
            r['d2_status']=decision['status'] if decision else 'not_sampled_remains_unknown';r['d2_reason']=decision['reason'] if decision else 'outside_bounded_evidence_repair_sample'
        pq.write_table(pa.Table.from_pylist(b.causes),out/'reference_root_causes.parquet',compression='zstd')
        linefile(out/'reference_sample_decisions.jsonl',repairs);linefile(out/'raw_price_evidence.jsonl',b.evidence)
        linefile(out/'boundary_stock_audit.jsonl',details);dump(out/'boundary_comparison.json',boundaries);dump(out/'breadth_date_differences.json',aggregate)
        dump(out/'lifecycle_quality.json',dict(policy='unchanged annotations; no deletion or denominator alteration',counts=b.lifecycle['counts'],unresolved=[r for r in b.lifecycle['rows'] if r['status']!='verified']))
        if any(sha(p)!=h for p,h in b.files.items()):raise ValueError('Input file changed during build')
        manifest=dict(version=VERSION,snapshot_id=key,mode='offline_D2_experiment',code_commit=None,active_switch=False,parents=b.pins,inputs_sha256=sha(out/'inputs.json'),summary_sha256=sha(out/'summary.json'),files={p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()})
        dump(out/'manifest.json',manifest)
        if target.exists():
            expected={p.name:sha(p) for p in out.iterdir()};actual={p.name:sha(p) for p in target.iterdir() if p.is_file()}
            if actual!=expected:raise ValueError('Existing immutable release differs from rebuild')
        else:shutil.copytree(out,target)
    b.db.close();print(canonical({'snapshot':str(target),'summary':summary}),flush=True)

if __name__=='__main__':main()
