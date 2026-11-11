"""Read-only inventory: source assets are never added to derived snapshot totals."""
import json
import hashlib
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime, timezone
import duckdb
from sqlalchemy import text
from app.research.queries import DEFAULT_ROOT

LABELS={'akshare':'AKShare / 东方财富','baostock':'BaoStock','tushare_proxy':'Tushare 兼容代理','tushare':'Tushare'}
ENDPOINTS={'limit_list_d':'涨跌停事件缓存','stock_zt_pool_em':'涨停名单','stock_zt_pool_zbgc_em':'炸板名单','stock_zt_pool_dtgc_em':'跌停名单','tool_trade_date_hist_sina':'交易日历','query_stock_basic':'股票基本信息','query_all_stock':'股票目录','query_history_k_data_plus':'日线','query_daily_history_k_AStock':'日线','stock_zh_a_hist':'日线'}

# Select a complete normalized batch per category, never a latest row per stock.
# Raw-only, failed and empty responses cannot erase a previously ingested pool.
POOL_SELECTION = """WITH batches AS (
 SELECT e.provider,e.endpoint,e.trade_date,e.limit_type,e.raw_id,max(r.fetched_at) fetched_at
 FROM limitup_events e JOIN raw_observations r ON r.id=e.raw_id
 WHERE e.provider=:p AND r.provider=e.provider AND r.endpoint=e.endpoint
   AND r.ingestion_status='success' AND e.ingestion_status='success'
 GROUP BY e.provider,e.endpoint,e.trade_date,e.limit_type,e.raw_id
), selected_batches AS (
 SELECT DISTINCT ON(provider,endpoint,trade_date,limit_type) * FROM batches
 ORDER BY provider,endpoint,trade_date,limit_type,fetched_at DESC,raw_id DESC
)
SELECT e.* FROM limitup_events e JOIN selected_batches b
 ON e.raw_id=b.raw_id AND e.provider=b.provider AND e.endpoint=b.endpoint
 AND e.trade_date=b.trade_date AND e.limit_type=b.limit_type
 WHERE e.ingestion_status='success'"""


def raw_hash_method(params,payload,status,expected):
    """Identify content hashing scope; legacy scope is not a corruption verdict."""
    for name,value in [('params_payload_status',dict(params=params,payload=payload,status=status)),
                       ('legacy_params_payload',dict(params=params,payload=payload))]:
        actual=hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if actual==expected:return name
    return 'unmatched'


RELEASES = {'U':'research/unified','H':'statistics/history_limits','E1':'statistics/market_breadth',
            'E2':'statistics/limit_structure','E3':'statistics/direction_signals','D1':'statistics/matched_cohort_breadth'}
PARENTS = {'source_snapshot':'U','history_snapshot':'H','breadth_snapshot':'E1',
           'limit_snapshot':'E2','e2_snapshot':'E2','old_e3_snapshot':'E3'}


def read_release(root,key,relative):
    base=(root/relative).resolve()
    node=dict(id=key,layer='standardized' if key=='U' else 'derived',snapshot_id=None,
              rule_version=None,generated_at=None,code_commit=None,code_hashes={},parents=[],fields=[],
              status='unavailable',reason=None,location=str(base),allowed_metrics=[],blocked_metrics=[])
    try:
        manifest=json.loads((base/'manifest.json').read_text())
        path=(base/manifest['dataset']).resolve()
        if not path.is_relative_to(base):raise ValueError('snapshot_path_outside_root')
        raw=(path/'summary.json').read_bytes()
        digest=hashlib.sha256(raw).hexdigest()
        expected=manifest.get('summary_sha256') or manifest.get('files',{}).get('summary.json')
        if expected and digest!=expected:raise ValueError('summary_hash_mismatch')
        summary=json.loads(raw)
        input_path=path/'inputs.json'
        inputs=json.loads(input_path.read_text()) if input_path.exists() else {}
        expected_input=summary.get('files',{}).get('inputs.json') or manifest.get('files',{}).get('inputs.json')
        if expected_input and hashlib.sha256(input_path.read_bytes()).hexdigest()!=expected_input:
            raise ValueError('inputs_hash_mismatch')
        node.update(snapshot_id=path.name,location=str(path),status='available',
            rule_version=summary.get('rule_version') or summary.get('version') or manifest.get('version'),
            generated_at=summary.get('computed_at_utc') or summary.get('built_at_utc'),
            code_commit=inputs.get('code_commit'),code_hashes=inputs.get('code_sha256',{}),
            date_min=inputs.get('start'),
            date_max=summary.get('data_end') or inputs.get('end'),
            manifest_sha256=hashlib.sha256((base/'manifest.json').read_bytes()).hexdigest(),
            summary_sha256=digest,integrity='summary_hash_checked' if expected else 'summary_digest_only_no_published_hash',
            integrity_scope='summary/inputs metadata only; source partitions are not revalidated by this request',
            raw_row_locator='未记录统一 raw 行定位；PG 可按 raw_id，文件按 source/代码/日期追溯',
            source_refs=[dict(location=p,sha256=h) for p,h in inputs.items() if key=='U' and isinstance(h,str) and len(h)==64])
        if key=='U':
            # Observed bounds from the actual immutable calendar, not an invented start.
            node['date_min']=None
            calendar=path/'calendar.parquet'
            if calendar.exists():
                with duckdb.connect() as d:
                    bounds=d.execute("SELECT min(trade_date),max(trade_date) FROM read_parquet(?) WHERE trade_date>=DATE '2017-01-01'",[str(calendar)]).fetchone()
                    node['date_min'],node['date_max']=bounds
        combined={**summary,**inputs}
        for field,parent in PARENTS.items():
            if combined.get(field):
                parent_path=Path(combined[field]).resolve()
                parent_base=(root/RELEASES[parent]).resolve()
                node['parents'].append(dict(id=parent,snapshot_id=parent_path.name,location=str(parent_path),
                    exists=parent_path.is_relative_to(parent_base) and parent_path.is_dir()))
        def quality(field,label,n,total,meaning,allowed,blocked,**extra):
            node['fields'].append(dict(field=field,label=label,available_count=n,denominator=total,
                status='unknown' if n is None else 'unavailable' if n==0 else 'scoped_available',
                meaning=meaning,allowed_metrics=allowed if n is not None and n>0 else [],blocked_metrics=blocked,
                evidence_snapshot=path.name,evidence_at=node['generated_at'],**extra))
        if key=='U':
            total=summary.get('rows')
            for field,label,metric,allow in [('price','基础价格','price_quality',['价格事实；收益还需参考价验证']),
                                            ('st_identity','ST 身份','st_quality',['仅已有身份来源证据的记录'])]:
                groups=summary.get('quality',{}).get(metric)
                counts={x['status']:x['n'] for x in groups} if groups is not None else {}
                quality(field,label,counts.get('verified'),total,
                    '源快照内部检查/证据状态；不是逐行交易所核验，也不是 CI-1 多源 corroborated',allow,
                    ['不可据此确认整行全部字段或全市场严格非 ST 资格'],counts=counts)
            for field,label in [('official_limit_up_price','官方涨停价'),('official_limit_down_price','官方跌停价')]:
                ratio=summary.get('field_coverage',{}).get(field)
                n=0 if ratio==0 else total if ratio==1 else None
                quality(field,label,n,total,'官方限价非空数量；供应商事件/规则推算不能替代',
                    [],['缺限价证据时不能确认严格涨跌停价'],reported_nonnull_ratio=ratio)
            node['allowed_metrics']=['按字段质量读取价格与证据；不是整行核验通过']
        elif key=='E1':
            t=summary.get('totals',{})
            quality('verified_return_reference','收益参考价适用',t.get('price_valid_count'),t.get('observed_count'),
                '沪深观察样本；close/已核验参考价；包含 ST 与身份未知；分母不同于 U 全部证据行',
                ['可观察样本广度','另过可比门禁后计算共同样本方向'],['全市场覆盖率','严格非 ST 限价与晋级'],
                counts={'unknown':t.get('price_unknown_count')})
            node.update(date_min=t.get('first_date') or node['date_min'],date_max=t.get('last_date') or node['date_max'])
        if key=='E1':node['allowed_metrics']=['可观察样本广度；跨日用途仍须经过可比性门禁']
        if key=='E2':node['allowed_metrics']=['同源供应商事件/晋级诊断，严格资格以逐日状态为准']
        if key=='E3':node['allowed_metrics']=['版本内通过门禁的描述性方向；诊断与严格分层']
        if key=='H':node['allowed_metrics']=['confirmed / candidate / unknown 分列的历史统计']
        if key=='D1':
            node['allowed_metrics']=['同一股票集合的两日广度；不是全市场趋势']
            node['availability']=summary.get('totals',{})
        node['blocked_metrics']=['不能据此宣称全市场完整覆盖或严格 point-in-time 回测']
        return node
    except (OSError,ValueError,KeyError,TypeError,duckdb.Error) as exc:
        node.update(status='unavailable',fields=[],allowed_metrics=[],reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
        return node


def snapshot_inventory(root):
    nodes=[read_release(Path(root),key,relative) for key,relative in RELEASES.items()]
    current={n['id']:n for n in nodes}
    for n in nodes:
        for p in n['parents']:
            p['matches_current_pointer']=p['snapshot_id']==current[p['id']]['snapshot_id']
        if any(not p['exists'] for p in n['parents']):
            n['status']='dependency_missing';n['allowed_metrics']=[]
            for f in n['fields']:f['status']='unknown';f['allowed_metrics']=[]
    return nodes


def rename(row):
    for old,new in [('day_count','days'),('month_key','month'),('row_count','rows'),('first_date','start'),('last_date','end')]:
        if old in row:row[new]=row.pop(old)
    return row


def parquet_inventory(root):
    items=[]
    for manifest_path in sorted((root/'processed/stocks/daily').glob('*/manifest.json')):
        manifest=json.loads(manifest_path.read_text());source=manifest.get('source',manifest_path.parent.name)
        paths=[];missing=[]
        for month,entry in manifest.get('months',{}).items():
            p=(manifest_path.parent/entry['file']).resolve()
            if not p.is_relative_to(manifest_path.parent.resolve()):raise ValueError('Invalid inventory path')
            if p.exists():paths.append(str(p))
            else:missing.append(month)
        staging=root/'staging/stocks/daily'/source/'manifest.json'
        staged=json.loads(staging.read_text()) if staging.exists() else {}
        item=dict(id='files:'+source,source='购买的历史数据包' if source.startswith('purchased_') else LABELS.get('akshare') if source.startswith('akshare_') else source,
            kind='历史日线 · 本地文件',storage='Parquet',rows=0,stocks=None,start=None,end=None,months=[],raw_rows=None,
            verification='已有局部校验记录；未完成全量交叉核验',notes=['月度文件存在不等于全市场完整覆盖。','未覆盖的股票、停牌与未上市原因尚未逐条核验。'],missing_files=missing,
            manifest_path=str(manifest_path),manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            staging_manifest_path=str(staging) if staged else None,
            staging_manifest_sha256=hashlib.sha256(staging.read_bytes()).hexdigest() if staged else None,
            source_id=source,archive=staged.get('archive'),archive_available=Path(staged['archive']).exists() if staged.get('archive') else None,
            staged_rows=sum(x.get('rows',0) for x in staged.get('months',{}).values()) if staged else None,
            import_duplicates=sum(x.get('duplicate_keys',0) for x in staged.get('months',{}).values()) if staged else None,
            import_invalid=sum(x.get('invalid_ohlcv_rows',0) for x in staged.get('months',{}).values()) if staged else None)
        if paths:
            with duckdb.connect(config={'threads':2,'memory_limit':'512MB'}) as db:
                rel=db.read_parquet(paths,union_by_name=True,hive_partitioning=False);rel.create_view('bars')
                cols=rel.columns
                missing_ref='count(*)' if 'pre_close' not in cols else 'count(*) FILTER(WHERE pre_close IS NULL)'
                missing_st='count(*)' if 'is_st' not in cols else 'count(*) FILTER(WHERE is_st IS NULL)'
                cur=db.execute(f"SELECT strftime(trade_date,'%Y-%m') AS month_key,count(*) AS row_count,count(DISTINCT stock_code) stocks,min(trade_date) AS first_date,max(trade_date) AS last_date,count(DISTINCT trade_date) AS day_count,{missing_ref} missing_reference,{missing_st} missing_st FROM bars GROUP BY 1 ORDER BY 1")
                item['months']=[rename(dict(zip([x[0] for x in cur.description],r))) for r in cur.fetchall()]
                item['rows'],item['stocks'],item['start'],item['end'],unique=db.execute('SELECT count(*),count(DISTINCT stock_code),min(trade_date),max(trade_date),count(DISTINCT (stock_code,trade_date)) FROM bars').fetchall()[0]
                item['duplicate_rows']=item['rows']-unique
        if source.startswith('purchased_'):item['notes'].append('原始压缩包尚未重新逐文件盘点；暂存行数来自导入清单，不是原包全部行数。')
        items.append(item)
    return items


def inventory(db,root=DEFAULT_ROOT):
    items=parquet_inventory(Path(root));warnings=[]
    # Persisted raw versions are counted as versions, never as distinct market records.
    raw=list(db.execute(text("""SELECT provider,endpoint,count(*) versions,
      sum(CASE WHEN json_typeof(raw_payload->'records')='array' THEN json_array_length(raw_payload->'records') ELSE 0 END) raw_rows,
      min(trade_date) AS first_date,max(trade_date) AS last_date,
      count(*) FILTER(WHERE ingestion_status='failed') failures
      FROM raw_observations GROUP BY provider,endpoint ORDER BY provider,endpoint""")).mappings())
    for provider in sorted({r['provider'] for r in raw}):
        for table,kind,extra in [('stock_daily','日线 · 数据库','adjustment'),('limitup_events','股票池事件 · 数据库','limit_type')]:
            count=db.execute(text(f'SELECT count(*) FROM {table} WHERE provider=:p'),{'p':provider}).scalar()
            if not count:continue
            # Daily revisions collapse by key; event revisions select the whole latest snapshot.
            if table=='stock_daily':
                cte=f"SELECT DISTINCT ON(stock_code,trade_date,{extra}) * FROM {table} WHERE provider=:p ORDER BY stock_code,trade_date,{extra},id DESC"
            else:
                cte=POOL_SELECTION
            group='limit_type' if table=='limitup_events' else 'adjustment'
            field_counts=",count(*) FILTER(WHERE pre_close IS NULL) missing_reference,count(*) FILTER(WHERE is_st IS NULL) missing_st" if table=='stock_daily' else ''
            records=list(db.execute(text(f"WITH selected AS ({cte}) SELECT {group} subgroup,to_char(trade_date,'YYYY-MM') AS month_key,count(*) AS row_count,count(DISTINCT stock_code) stocks,min(trade_date) AS first_date,max(trade_date) AS last_date,count(DISTINCT trade_date) AS day_count {field_counts} FROM selected GROUP BY 1,2 ORDER BY 1,2"),{'p':provider}).mappings())
            for subgroup in sorted({r['subgroup'] for r in records}):
                months=[rename(dict(r)) for r in records if r['subgroup']==subgroup]
                stocks=db.execute(text(f'WITH selected AS ({cte}) SELECT count(DISTINCT stock_code) FROM selected WHERE {group}=:g'),{'p':provider,'g':subgroup}).scalar()
                items.append(dict(id=f'pg:{provider}:{table}:{subgroup}',source=LABELS.get(provider,provider),kind=({'up':'涨停名单','down':'跌停名单','broken':'炸板名单'}.get(subgroup,kind+(' · 未复权' if subgroup=='none' else ' · '+str(subgroup)))),storage='PostgreSQL',rows=sum(r['rows'] for r in months),stocks=stocks,start=min(r['start'] for r in months),end=max(r['end'] for r in months),months=months,verification='入库格式校验；未完成全量交叉核验',notes=['仅代表此来源已保存的记录，不代表全市场完整覆盖。','同源日线修订按最新记录展示；股票池按整批最新成功快照展示。'],source_id=provider))
    hash_counts=defaultdict(Counter)
    for r in db.execute(text('SELECT provider,endpoint,parameters,raw_payload,ingestion_status,sha256 FROM raw_observations')).mappings():
        hash_counts[(r['provider'],r['endpoint'])][raw_hash_method(r['parameters'],r['raw_payload'],r['ingestion_status'],r['sha256'])]+=1
    # Raw evidence is exposed separately, including Tushare caches not normalized into tables.
    for r in raw:
        items.append(dict(id=f"raw:{r['provider']}:{r['endpoint']}",source=LABELS.get(r['provider'],r['provider']),kind=ENDPOINTS.get(r['endpoint'],r['endpoint'])+' · 原始响应',storage='原始缓存',hash_method_counts=dict(hash_counts[(r['provider'],r['endpoint'])]),rows=None,raw_rows=r['raw_rows'],versions=r['versions'],failures=r['failures'],stocks=None,start=r['first_date'],end=r['last_date'],months=[],verification='原始响应已保存；不等于核验通过',notes=['响应行数包含重复请求及历史版本，不能与入库行数相加。','日期是请求/归档日期，不一定是响应内行情的起止日期。'],source_id=r['provider']+':'+r['endpoint']))
    calendar=[r[0] for r in db.execute(text("SELECT DISTINCT trade_date FROM trading_days WHERE exchange='SSE' ORDER BY trade_date"))]
    for item in items:
        if not item['months']:item['missing_days']=None;continue
        lo,hi=item['start'],item['end']
        known=bool(calendar and calendar[0]<=lo<=hi<=calendar[-1])
        expected={}
        if known:
            for d in calendar:
                if lo<=d<=hi:expected[str(d)[:7]]=expected.get(str(d)[:7],0)+1
        months={m['month']:m for m in item['months']}
        for month in expected:
            if month not in months:months[month]=dict(month=month,rows=0,stocks=0,days=0)
        for month,m in months.items():
            m['expected_days']=expected.get(month,0) if known else None
            m['missing_days']=max(0,m['expected_days']-m['days']) if known else None
        item['months']=[months[k] for k in sorted(months)]
        item['missing_days']=sum(m['missing_days'] for m in item['months']) if known else None
    return dict(items=items,snapshots=snapshot_inventory(Path(root)),as_of=datetime.now(timezone.utc),warnings=warnings,
        scope='已登记的本地日线文件与数据库原始响应、规范化记录；未登记散落文件尚未盘点。研究快照按依赖单列，不重复计入来源总量。')
