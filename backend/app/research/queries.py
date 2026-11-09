"""Coverage-aware research queries over separately audited local Parquet sources."""
from datetime import date
import json
from pathlib import Path

import duckdb
from app.research.limit_rules import FACTOR_POLICY

DEFAULT_ROOT=Path.home()/'Datasets/asharedragon'


class Research:
    def __init__(self,root=DEFAULT_ROOT):
        self.root=Path(root).resolve()
        catalog_path=self.root/'research/catalog.json'
        if not catalog_path.exists():raise ValueError('Research coverage catalogue has not been built')
        self.catalog=json.loads(catalog_path.read_text())
        self.db=duckdb.connect(config={'memory_limit':'512MB','threads':'2'})
        try:
            paths=[]
            for relative in self.catalog['datasets']:
                dataset=self.safe_path(relative)
                manifest=json.loads((dataset/'manifest.json').read_text())
                if manifest.get('status')!='validated_partial' or manifest.get('adjustment_type')!='none':
                    raise ValueError('Unvalidated research source')
                for item in manifest['months'].values():
                    path=(dataset/item['file']).resolve()
                    if not path.is_relative_to(dataset):raise ValueError('Partition escaped dataset')
                    paths.append(str(path))
            raw=self.db.read_parquet(paths,union_by_name=True,hive_partitioning=False)
            raw.create_view('raw_daily')
            additions=[f'NULL::BOOLEAN AS {name}' for name in ('trade_status','is_st') if name not in raw.columns]
            self.db.sql('SELECT *'+(' ,'+','.join(additions) if additions else '')+' FROM raw_daily').create_view('priced_daily')
            status_path=self.safe_path('research/status_evidence.parquet')
            if status_path.exists():self.db.read_parquet(str(status_path)).create_view('status_evidence')
            else:self.db.sql('SELECT NULL::VARCHAR AS stock_code,NULL::DATE AS trade_date,NULL::BOOLEAN AS trade_status,NULL::BOOLEAN AS is_st,NULL::VARCHAR AS provider WHERE false').create_view('status_evidence')
            self.db.sql('''SELECT d.* EXCLUDE(trade_status,is_st),
                coalesce(d.trade_status,s.trade_status) AS trade_status,
                coalesce(d.is_st,s.is_st) AS is_st,
                CASE WHEN d.trade_status IS NOT NULL OR d.is_st IS NOT NULL THEN d.source ELSE s.provider END AS status_source
                FROM priced_daily d LEFT JOIN status_evidence s USING(stock_code,trade_date)''').create_view('daily')
            for name in ('calendar','stocks','events'):
                self.db.read_parquet(str(self.safe_path(f'research/{name}.parquet'))).create_view(name)
        except Exception:
            self.db.close();raise

    def safe_path(self,relative):
        path=(self.root/relative).resolve()
        if not path.is_relative_to(self.root):raise ValueError('Research path escaped root')
        return path

    def close(self):self.db.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

    def query(self,sql,params=None):
        result=self.db.execute(sql,params or [])
        columns=[c[0] for c in result.description]
        return [dict(zip(columns,row)) for row in result.fetchall()]

    def bounds(self,start,end):
        if start>end:raise ValueError('start must not exceed end')
        if str(start)<self.catalog['start'] or str(end)>self.catalog['end']:
            raise ValueError('Requested dates outside audited calendar range')

    def meta(self):
        return {'factor_policy':FACTOR_POLICY.copy(),'data_kind':'observed_daily','coverage_scope':'SH/SZ provider catalogue, not certified whole-market completeness',
            'bse_status':'out_of_scope_not_collected','snapshot_at':self.catalog['snapshot_at'],
            'calendar_end':self.catalog['end'],'lifecycle_evidence':self.catalog['lifecycle_evidence'],
            'warning':'Absent rows are unresolved gaps; do not infer suspension or zero prices. Prices are unadjusted; no formal dragon labels.'}

    def stock(self,code,start,end):
        self.bounds(start,end)
        data=self.query('SELECT * FROM daily WHERE stock_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date',[code,start,end])
        missing=self.query('''SELECT c.trade_date FROM calendar c ANTI JOIN
            (SELECT trade_date FROM daily WHERE stock_code=?) d USING(trade_date)
            WHERE c.trade_date BETWEEN ? AND ? ORDER BY c.trade_date''',[code,start,end])
        lifecycle=self.query('SELECT * FROM stocks WHERE stock_code=?',[code])
        expected=self.query('''SELECT count(*) AS days FROM calendar c JOIN stocks s ON
            c.trade_date>=s.listing_date AND (s.delisting_date IS NULL OR c.trade_date<=s.delisting_date)
            WHERE s.stock_code=? AND c.trade_date BETWEEN ? AND ?''',[code,start,end])[0]['days'] if lifecycle else None
        unresolved=self.query('''SELECT c.trade_date FROM calendar c JOIN stocks s ON
            c.trade_date>=s.listing_date AND (s.delisting_date IS NULL OR c.trade_date<=s.delisting_date)
            ANTI JOIN (SELECT trade_date FROM daily WHERE stock_code=?) d USING(trade_date)
            WHERE s.stock_code=? AND c.trade_date BETWEEN ? AND ? ORDER BY c.trade_date''',[code,code,start,end]) if lifecycle else missing
        known_states={r['trade_date']:r for r in self.query('SELECT trade_date,trade_status,is_st,provider FROM status_evidence WHERE stock_code=? AND trade_date BETWEEN ? AND ?',[code,start,end])}
        details=[]
        for item in unresolved:
            state=known_states.get(item['trade_date'],{})
            details.append({**item,'trade_status':state.get('trade_status'),'is_st':state.get('is_st'),'status_source':state.get('provider'),
                'reason':'observed_suspension' if state.get('trade_status') is False else 'missing_despite_observed_trading_status' if state.get('trade_status') is True else 'unresolved'})
        return {'data':data,'meta':{**self.meta(),'stock_code':code,'lifecycle':lifecycle,
            'missing_active_details':details,
            'observed_rows':len(data),'expected_active_sessions':expected,
            'missing_active_dates':[r['trade_date'] for r in unresolved],
            'dates_without_observations':[r['trade_date'] for r in missing],
            'coverage_status':'unknown_lifecycle' if not lifecycle or lifecycle[0]['listing_date'] is None else 'outside_known_lifecycle' if expected==0 else 'partial' if unresolved else 'covered_within_known_lifecycle'}}

    def market(self,day,page=1,page_size=200):
        self.bounds(day,day)
        counts=self.query('''SELECT (SELECT count(*) FROM daily WHERE trade_date=?) AS observed,
            (SELECT count(*) FROM stocks WHERE listing_date<=? AND (delisting_date IS NULL OR delisting_date>=?)) AS expected,
            (SELECT count(*) FROM calendar WHERE trade_date=?) AS is_session''',[day,day,day,day])[0]
        missing=self.query('''SELECT stock_code FROM stocks WHERE listing_date<=? AND (delisting_date IS NULL OR delisting_date>=?)
            AND stock_code NOT IN (SELECT stock_code FROM daily WHERE trade_date=?) ORDER BY stock_code''',[day,day,day]) if counts['is_session'] else []
        data=self.query('SELECT * FROM daily WHERE trade_date=? ORDER BY stock_code LIMIT ? OFFSET ?',[day,page_size,(page-1)*page_size])
        return {'data':data,'meta':{**self.meta(),'trade_date':day,'page':page,'page_size':page_size,
            'observed_stocks':counts['observed'] if counts['is_session'] else None,
            'expected_active_stocks':counts['expected'] if counts['is_session'] else None,
            'missing_stock_codes':[r['stock_code'] for r in missing],
            'coverage_status':'non_trading_day' if not counts['is_session'] else 'missing' if not counts['observed'] else 'partial' if missing else 'covered_known_catalogue'}}

    def monthly(self,start,end):
        self.bounds(start,end)
        data=self.query('''WITH sessions AS (SELECT * FROM calendar WHERE trade_date BETWEEN ? AND ?),
            expected AS (SELECT strftime(c.trade_date,'%Y-%m') AS month,count(*) AS expected_stock_sessions
                FROM sessions c JOIN stocks s ON c.trade_date>=s.listing_date AND
                (s.delisting_date IS NULL OR c.trade_date<=s.delisting_date) GROUP BY 1),
            matched AS (SELECT strftime(d.trade_date,'%Y-%m') AS month,count(*) AS matched_stock_sessions
                FROM daily d JOIN stocks s USING(stock_code) WHERE d.trade_date BETWEEN ? AND ?
                AND d.trade_date>=s.listing_date AND (s.delisting_date IS NULL OR d.trade_date<=s.delisting_date) GROUP BY 1),
            observed AS (SELECT strftime(trade_date,'%Y-%m') AS month,count(*) AS rows,count(DISTINCT stock_code) AS stocks,
                count(DISTINCT trade_date) AS covered_days FROM daily WHERE trade_date BETWEEN ? AND ? GROUP BY 1),
            months AS (SELECT strftime(trade_date,'%Y-%m') AS month,count(*) AS expected_days FROM sessions GROUP BY 1)
            SELECT m.*,o.rows,o.stocks,coalesce(o.covered_days,0) AS covered_days,e.expected_stock_sessions,
                mt.matched_stock_sessions,coalesce(o.covered_days,0)*1.0/m.expected_days AS date_coverage,
                mt.matched_stock_sessions*1.0/nullif(e.expected_stock_sessions,0) AS known_catalogue_coverage,
                CASE WHEN o.rows IS NULL THEN 'missing' WHEN coalesce(mt.matched_stock_sessions,0)<e.expected_stock_sessions THEN 'partial' ELSE 'covered_known_catalogue' END AS coverage_status
            FROM months m LEFT JOIN observed o USING(month) LEFT JOIN expected e USING(month)
            LEFT JOIN matched mt USING(month) ORDER BY month''',[start,end,start,end,start,end])
        return {'data':data,'meta':self.meta()}

    def preboard(self,code,event_date):
        self.bounds(event_date,event_date)
        event=self.query("SELECT * FROM events WHERE stock_code=? AND trade_date=? AND limit_type='up' AND consecutive_limit_days=1",[code,event_date])
        rows=self.query('''WITH sessions AS (SELECT trade_date FROM calendar WHERE trade_date<? ORDER BY trade_date DESC LIMIT 20)
            SELECT c.trade_date,d.open,d.high,d.low,d.close,d.volume,d.amount,d.source,d.adjustment_type,
                coalesce(d.trade_status,s.trade_status) AS trade_status,coalesce(d.is_st,s.is_st) AS is_st,
                coalesce(d.status_source,s.provider) AS status_source
            FROM sessions c LEFT JOIN daily d ON c.trade_date=d.trade_date AND d.stock_code=?
            LEFT JOIN status_evidence s ON s.trade_date=c.trade_date AND s.stock_code=?
            ORDER BY c.trade_date''',[event_date,code,code])
        windows={}
        for n in (5,10,20):
            window=rows[-n:];missing=[r['trade_date'] for r in window if r['close'] is None]
            complete=len(window)==n and not missing
            windows[str(n)]={'rows':window,'missing_dates':missing,'coverage_status':'complete_observations' if complete else 'partial',
                'features':{'mean_volume':sum(r['volume'] for r in window)/n,'mean_amount':sum(r['amount'] for r in window)/n,
                    'highest_price':max(r['high'] for r in window),'lowest_price':min(r['low'] for r in window)} if complete else None}
        return {'data':windows,'meta':{**self.meta(),'stock_code':code,'event_date':event_date,
            'first_board_confirmed_by_local_tushare':bool(event),'event_evidence':event,
            'window_basis':'previous exchange sessions, excluding event day; no forward fill',
            'event_status':'observed_first_board' if event else 'unconfirmed_candidate_date'}}

    def events(self,start,end,code=None,page=1,page_size=200):
        self.bounds(start,end)
        condition='AND e.stock_code=?' if code else ''
        params=[start,end]+([code] if code else [])
        data=self.query(f'''SELECT e.*,d.open,d.high,d.low,d.close,d.volume,d.amount,d.source AS daily_source,
            CASE WHEN d.stock_code IS NULL THEN 'daily_missing' ELSE 'daily_present' END AS coverage_status
            FROM events e LEFT JOIN daily d USING(stock_code,trade_date)
            WHERE e.trade_date BETWEEN ? AND ? {condition} ORDER BY e.trade_date,e.stock_code,e.limit_type LIMIT ? OFFSET ?''',params+[page_size,(page-1)*page_size])
        count=self.query(f'SELECT count(*) AS n FROM events e WHERE e.trade_date BETWEEN ? AND ? {condition}',params)[0]['n']
        return {'data':data,'meta':{**self.meta(),'total_events':count,'page':page,'page_size':page_size,
            'event_coverage':'local Tushare observations only; no events does not mean no limit-ups',
            'event_date_range':self.catalog['event_date_range']}}
