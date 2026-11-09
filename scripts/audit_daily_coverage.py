"""Refresh local research catalogue and audit lifecycle coverage, without paid requests."""
from datetime import date, datetime, timezone
import json
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from app.db.session import engine
from app.research.queries import Research
from extend_daily_2026 import ROOT, LOCAL, atomic, universe


def write_table(path,rows,schema):
    pq.write_table(pa.Table.from_pylist(rows,schema=schema),path,compression='zstd')


def build():
    destination=ROOT/'research';destination.mkdir(parents=True,exist_ok=True)
    # Local DB reads are one read-only snapshot; no update/insert statements.
    with engine.connect() as c, c.begin():
        c.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        def read(q):return [dict(r) for r in c.execute(text(q)).mappings()]
        cal=read("SELECT DISTINCT trade_date FROM trading_days WHERE exchange='SSE' AND trade_date<='2026-11-06' ORDER BY trade_date")
        stock_rows=read("SELECT DISTINCT ON(stock_code) stock_code,stock_name,listing_date,delisting_date,status,provider,collected_at FROM stocks WHERE provider='baostock' ORDER BY stock_code,collected_at DESC,id DESC")
        events=read("SELECT DISTINCT ON(stock_code,trade_date,limit_type) stock_code,trade_date,limit_type,consecutive_limit_days,provider FROM limitup_events WHERE provider='tushare_proxy' ORDER BY stock_code,trade_date,limit_type,id DESC")
        states=read("SELECT DISTINCT ON(stock_code,trade_date) stock_code,trade_date,trade_status,is_st,provider FROM stock_daily WHERE provider='baostock' ORDER BY stock_code,trade_date,id DESC")
    atomic(LOCAL/'stocks.json',stock_rows)
    universe()
    stocks=json.loads((LOCAL/'universe.json').read_text())
    for row in stocks:
        for field in ('listing_date','delisting_date'):
            row[field]=date.fromisoformat(row[field]) if row[field] else None
        c=row['stock_code']
        row['board']='star' if c.startswith('688') else 'chinext' if c.startswith(('300','301')) else 'sh_main' if c.endswith('.SH') else 'sz_main'
    write_table(destination/'calendar.parquet',cal,pa.schema([('trade_date',pa.date32())]))
    write_table(destination/'stocks.parquet',stocks,pa.schema([('stock_code',pa.string()),('stock_name',pa.string()),('listing_date',pa.date32()),('delisting_date',pa.date32()),('status',pa.string()),('provider',pa.string()),('board',pa.string())]))
    write_table(destination/'events.parquet',events,pa.schema([('stock_code',pa.string()),('trade_date',pa.date32()),('limit_type',pa.string()),('consecutive_limit_days',pa.int64()),('provider',pa.string())]))
    write_table(destination/'status_evidence.parquet',states,pa.schema([('stock_code',pa.string()),('trade_date',pa.date32()),('trade_status',pa.bool_()),('is_st',pa.bool_()),('provider',pa.string())]))
    datasets=[]
    for path in sorted((ROOT/'processed/stocks/daily').glob('*/manifest.json')):
        manifest=json.loads(path.read_text())
        if manifest['status']=='validated_partial' and manifest['adjustment_type']=='none':datasets.append(str(path.parent.relative_to(ROOT)))
    catalog={'datasets':datasets,'start':'2017-01-01','end':'2026-11-06','snapshot_at':datetime.now(timezone.utc).isoformat(),
        'lifecycle_evidence':'Local BaoStock historical catalogue (2026-11-05) plus available live exchange rosters; surviving-only rosters never replace delisted history',
        'roster_status':{tag:('available' if (LOCAL/f'roster_{tag}.json').exists() else 'unavailable') for tag in ('sh_main','sh_star','sz')},
        'event_date_range':{'first':str(min(r['trade_date'] for r in events)) if events else None,'last':str(max(r['trade_date'] for r in events)) if events else None}}
    atomic(destination/'catalog.json',catalog)
    with Research(ROOT) as r:
        r.db.read_parquet(str(destination/'status_evidence.parquet')).create_view('status_evidence')
        r.db.sql("SET temp_directory='"+str(destination/'tmp').replace("'","''")+"'")
        # Cross-source duplicates must halt the audit, not silently select a winner.
        duplicate=r.query('''SELECT count(*) AS n FROM (SELECT stock_code,trade_date FROM daily GROUP BY ALL HAVING count(*)>1)''')[0]['n']
        if duplicate:raise ValueError('Overlapping daily sources require explicit reconciliation')
        r.db.sql('''SELECT s.stock_code,s.board,s.listing_date,s.delisting_date,c.trade_date,
            CASE WHEN st.trade_status=false THEN 'observed_suspended_baostock' ELSE 'missing_unresolved' END AS missing_reason
            FROM calendar c JOIN stocks s ON c.trade_date>=s.listing_date AND
                (s.delisting_date IS NULL OR c.trade_date<=s.delisting_date)
            LEFT JOIN status_evidence st ON st.stock_code=s.stock_code AND st.trade_date=c.trade_date
            WHERE c.trade_date BETWEEN DATE '2017-01-01' AND DATE '2026-11-06'
            AND NOT EXISTS (SELECT 1 FROM daily d WHERE d.stock_code=s.stock_code AND d.trade_date=c.trade_date)
            ''').write_parquet(str(destination/'missing_stock_dates.parquet'),compression='zstd')
        r.db.read_parquet(str(destination/'missing_stock_dates.parquet')).create_view('missing')
        summary={
            'catalog':catalog,'duplicate_keys':duplicate,
            'yearly':r.query('''WITH observed AS (SELECT year(trade_date) AS year,count(*) AS rows,count(DISTINCT stock_code) AS stocks,
                count(DISTINCT trade_date) AS days FROM daily GROUP BY 1),
                expected AS (SELECT year(trade_date) AS year,count(*) AS days FROM calendar WHERE trade_date BETWEEN DATE '2017-01-01' AND DATE '2026-11-06' GROUP BY 1)
                SELECT e.year,o.rows,o.stocks,coalesce(o.days,0) AS observed_days,e.days AS expected_days,coalesce(o.days,0)*1.0/e.days AS date_coverage FROM expected e LEFT JOIN observed o USING(year) ORDER BY 1'''),
            'missing_dates':r.query("SELECT trade_date FROM calendar WHERE trade_date BETWEEN DATE '2017-01-01' AND DATE '2026-11-06' AND trade_date NOT IN (SELECT trade_date FROM daily) ORDER BY 1"),
            'missing_by_year_board':r.query('SELECT year(trade_date) AS year,board,count(*) AS missing_stock_sessions,count(DISTINCT stock_code) AS stocks FROM missing GROUP BY 1,2 ORDER BY 1,2'),
            'absent_stocks':r.query("SELECT * FROM stocks WHERE listing_date<=DATE '2026-11-06' AND (delisting_date IS NULL OR delisting_date>=DATE '2017-01-01') AND stock_code NOT IN (SELECT stock_code FROM daily) ORDER BY stock_code"),
            'new_2026':r.query("SELECT s.*,count(d.trade_date) AS observed_rows,min(d.trade_date) AS first_observation,max(d.trade_date) AS last_observation FROM stocks s LEFT JOIN daily d USING(stock_code) WHERE year(s.listing_date)=2026 GROUP BY ALL ORDER BY s.listing_date,s.stock_code"),
            'board_coverage':r.query('''SELECT s.board,count(DISTINCT s.stock_code) AS observed_stocks,count(*) AS rows FROM daily d JOIN stocks s USING(stock_code) GROUP BY 1'''),
            'delisted':r.query("SELECT count(*) AS known_delisted,count(*) FILTER(WHERE stock_code IN (SELECT stock_code FROM daily)) AS observed FROM stocks WHERE delisting_date BETWEEN DATE '2017-01-01' AND DATE '2026-11-06'"),
            'lifecycle_anomalies':r.query('''SELECT count(*) AS outside_known_lifecycle FROM daily d JOIN stocks s USING(stock_code) WHERE d.trade_date<s.listing_date OR d.trade_date>s.delisting_date'''),
            'unknown_stocks':r.query('SELECT DISTINCT stock_code FROM daily ANTI JOIN stocks USING(stock_code) ORDER BY 1'),
            'status_evidence':r.query('SELECT count(*) AS rows,count(*) FILTER(WHERE trade_status=false) AS suspended,count(DISTINCT stock_code) AS stocks FROM status_evidence'),
            'resume_transitions':r.query('''WITH sessions AS (SELECT trade_date,lag(trade_date) OVER(ORDER BY trade_date) AS previous FROM calendar)
                SELECT count(*) AS verified_resumptions FROM status_evidence current JOIN sessions USING(trade_date)
                JOIN status_evidence previous ON previous.stock_code=current.stock_code AND previous.trade_date=sessions.previous
                WHERE current.trade_status=true AND previous.trade_status=false'''),
            'missing_reasons':r.query('SELECT missing_reason,count(*) AS rows FROM missing GROUP BY 1'),
            'price_quality':r.query('''SELECT count(*) FILTER(WHERE close IS NULL OR open IS NULL OR high IS NULL OR low IS NULL OR
                NOT isfinite(close) OR NOT isfinite(open) OR NOT isfinite(high) OR NOT isfinite(low) OR
                close<=0 OR open<=0 OR high<=0 OR low<=0 OR high<greatest(open,close,low) OR low>least(open,close)) AS invalid_ohlc FROM daily'''),
            'unit_sanity':r.query('''SELECT source,count(*) AS rows,count(*) FILTER(WHERE volume=0) AS zero_volume_rows,count(*) FILTER(WHERE amount=0) AS zero_amount_rows,
                count(*) FILTER(WHERE volume>1000 AND (amount/volume<low-0.02 OR amount/volume>high+0.02)) AS implied_price_outliers,
                count(*) FILTER(WHERE volume<0 OR amount<0 OR NOT isfinite(volume) OR NOT isfinite(amount) OR volume IS NULL OR amount IS NULL) AS invalid_volume_amount
                FROM daily GROUP BY source'''),
            'extension':r.query("SELECT source,count(*) AS rows,count(DISTINCT stock_code) AS stocks,count(DISTINCT trade_date) AS days,min(trade_date) AS first_date,max(trade_date) AS last_date FROM daily WHERE trade_date>=DATE '2026-03-16' GROUP BY source"),
            'monthly':r.monthly(date(2017,1,1),date(2026,11,6))['data']}
        r.db.sql('SELECT stock_code,board,count(*) AS missing_sessions,min(trade_date) AS first_missing,max(trade_date) AS last_missing FROM missing GROUP BY 1,2 ORDER BY 1').write_parquet(str(destination/'missing_stocks_summary.parquet'),compression='zstd')
        atomic(destination/'coverage_audit.json',summary)
        print('Audit:',destination/'coverage_audit.json',flush=True)
        print('Extension:',summary['extension'],flush=True)


if __name__=='__main__':build()
