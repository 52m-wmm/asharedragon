"""Read-only inventory: source assets are never added to derived snapshot totals."""
import json
from pathlib import Path
from datetime import datetime, timezone
import duckdb
from sqlalchemy import text
from app.research.queries import DEFAULT_ROOT

LABELS={'akshare':'AKShare / 东方财富','baostock':'BaoStock','tushare_proxy':'Tushare 兼容代理','tushare':'Tushare'}
ENDPOINTS={'limit_list_d':'涨跌停事件缓存','stock_zt_pool_em':'涨停名单','stock_zt_pool_zbgc_em':'炸板名单','stock_zt_pool_dtgc_em':'跌停名单','tool_trade_date_hist_sina':'交易日历','query_stock_basic':'股票基本信息','query_all_stock':'股票目录','query_history_k_data_plus':'日线','query_daily_history_k_AStock':'日线','stock_zh_a_hist':'日线'}


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
                cte="""SELECT e.* FROM limitup_events e JOIN
                (SELECT DISTINCT ON(trade_date,endpoint) id FROM raw_observations WHERE provider=:p AND ingestion_status='success' AND EXISTS (SELECT 1 FROM limitup_events stored WHERE stored.raw_id=raw_observations.id) ORDER BY trade_date,endpoint,fetched_at DESC,id DESC) r ON e.raw_id=r.id WHERE e.provider=:p"""
            group='limit_type' if table=='limitup_events' else 'adjustment'
            field_counts=",count(*) FILTER(WHERE pre_close IS NULL) missing_reference,count(*) FILTER(WHERE is_st IS NULL) missing_st" if table=='stock_daily' else ''
            records=list(db.execute(text(f"WITH selected AS ({cte}) SELECT {group} subgroup,to_char(trade_date,'YYYY-MM') AS month_key,count(*) AS row_count,count(DISTINCT stock_code) stocks,min(trade_date) AS first_date,max(trade_date) AS last_date,count(DISTINCT trade_date) AS day_count {field_counts} FROM selected GROUP BY 1,2 ORDER BY 1,2"),{'p':provider}).mappings())
            for subgroup in sorted({r['subgroup'] for r in records}):
                months=[rename(dict(r)) for r in records if r['subgroup']==subgroup]
                stocks=db.execute(text(f'WITH selected AS ({cte}) SELECT count(DISTINCT stock_code) FROM selected WHERE {group}=:g'),{'p':provider,'g':subgroup}).scalar()
                items.append(dict(id=f'pg:{provider}:{table}:{subgroup}',source=LABELS.get(provider,provider),kind=({'up':'涨停名单','down':'跌停名单','broken':'炸板名单'}.get(subgroup,kind+(' · 未复权' if subgroup=='none' else ' · '+str(subgroup)))),storage='PostgreSQL',rows=sum(r['rows'] for r in months),stocks=stocks,start=min(r['start'] for r in months),end=max(r['end'] for r in months),months=months,verification='入库格式校验；未完成全量交叉核验',notes=['仅代表此来源已保存的记录，不代表全市场完整覆盖。','同源日线修订按最新记录展示；股票池按整批最新成功快照展示。'],source_id=provider))
    # Raw evidence is exposed separately, including Tushare caches not normalized into tables.
    for r in raw:
        items.append(dict(id=f"raw:{r['provider']}:{r['endpoint']}",source=LABELS.get(r['provider'],r['provider']),kind=ENDPOINTS.get(r['endpoint'],r['endpoint'])+' · 原始响应',storage='原始缓存',rows=None,raw_rows=r['raw_rows'],versions=r['versions'],failures=r['failures'],stocks=None,start=r['first_date'],end=r['last_date'],months=[],verification='原始响应已保存；不等于核验通过',notes=['响应行数包含重复请求及历史版本，不能与入库行数相加。','日期是请求/归档日期，不一定是响应内行情的起止日期。'],source_id=r['provider']+':'+r['endpoint']))
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
    return dict(items=items,as_of=datetime.now(timezone.utc),warnings=warnings,
        scope='已登记的本地日线文件与数据库原始响应、规范化记录；未登记散落文件尚未盘点。研究快照不重复计入。')
