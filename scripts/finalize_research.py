"""Offline, immutable research release. Only SELECTs against local PostgreSQL."""
from datetime import date, datetime, timezone
from pathlib import Path
import argparse
import fcntl
import hashlib
import json
import shutil

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text
from app.db.session import engine
from app.research.queries import Research, DEFAULT_ROOT

VERSION = 'research-final-v1'


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, default=str, indent=2, allow_nan=False))


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def outcome(value):
    return 'success' if value is True else 'failure' if value is False else 'unknown'


def build(root=DEFAULT_ROOT, end=None):
    from app.services.calendar import completed_date
    root = Path(root).resolve()
    target = root / 'research/unified'
    if end is None:
        current=json.loads((target/'manifest.json').read_text())
        current_path=(target/current['dataset']).resolve()
        if not current_path.is_relative_to(target):raise ValueError('Invalid snapshot path')
        end=date.fromisoformat(json.loads((current_path/'summary.json').read_text())['data_end'])
    if end>completed_date():raise ValueError('截止日尚未收盘')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out = target / 'snapshots' / stamp
    out.mkdir(parents=True)
    inputs = {}
    def track(path):
        inputs[str(path)] = sha(path)
        return path
    def snapshot(relative):
        base = root / relative
        manifest = json.loads(track(base / 'manifest.json').read_text())
        folder = (base / manifest.get('dataset', '.')).resolve()
        if not folder.is_relative_to(base):
            raise ValueError('Snapshot escaped root')
        for name, expected in manifest['files'].items():
            p = (folder / name).resolve()
            if not p.is_relative_to(folder) or sha(p) != expected:
                raise ValueError(f'Input hash mismatch: {p}')
            track(p)
        return folder
    def copy(path, name):
        track(path)
        shutil.copy2(path, out / (name + '.parquet'))
    print('Snapshotting local evidence and PostgreSQL', flush=True)
    # Preserve original primary/raw IDs; no DB credentials in release artifacts.
    with engine.connect() as conn, conn.begin():
        conn.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        pg_snapshot = conn.execute(text('select txid_current_snapshot()::text')).scalar()
        calendar = pd.read_sql(text("SELECT DISTINCT trade_date FROM trading_days WHERE exchange='SSE' AND trade_date<=:end ORDER BY trade_date"),conn,params={'end':end})
        if calendar.empty or calendar.iloc[-1]['trade_date'] != end:
            raise ValueError('截止日不在本地交易日历中')
        pq.write_table(pa.Table.from_pandas(calendar,preserve_index=False),out/'calendar.parquet',compression='zstd')
        for name in ('stock_daily', 'limitup_events', 'market_sentiment_daily', 'themes', 'stock_themes', 'data_coverage'):
            frame = pd.read_sql(text('SELECT * FROM ' + name), conn)
            pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), out / ('pg_' + name + '.parquet'), compression='zstd')
        frame = pd.read_sql(text('SELECT id,provider,endpoint,trade_date,fetched_at,ingestion_status FROM raw_observations'), conn)
        pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), out / 'pg_raw_links.parquet', compression='zstd')
    for name in ('stocks', 'status_evidence'):
        copy(root / f'research/{name}.parquet', name)
    facts_dir = snapshot('research/dragon_facts')
    cycles_dir = snapshot('research/dragon_v1')
    status_dir = snapshot('research/dragon_v053')
    cross_dir = snapshot('cross_market')
    latest_dir = snapshot('research/dragon_v056')
    features_dir = snapshot('research/dragon_feature_v06')
    copy(facts_dir / 'stock_day_evidence.parquet', 'prior_day_evidence')
    copy(latest_dir / 'stock_day_evidence.parquet', 'latest_day_evidence')
    for name in ('historical_stock_status', 'security_aliases'):
        copy(status_dir / (name + '.parquet'), name)
    for p in cross_dir.glob('*.parquet'):
        copy(p, 'cross_' + p.stem)
    for p in (root / 'research/historical_limits_v1').iterdir():
        if p.is_file(): track(p)
    # Archive revisions remain independently addressable; never concatenate into daily rows.
    archives = []
    local = Path(__file__).resolve().parents[1] / 'data'
    for p in sorted((local / 'raw/tushare').rglob('*.json')):
        archives.append({'path': str(p), 'sha256': sha(p), 'bytes': p.stat().st_size})
    dump(out / 'archive_inventory.json', archives)
    # Legacy diagnostics are retained, never added to the latest population.
    copy(cycles_dir / 'cycles.parquet', 'legacy_cycles')
    facts = json.loads((latest_dir / 'facts.json').read_text())
    samples = json.loads((latest_dir / 'samples.json').read_text())
    by_id = {f['fact_id']:f for f in facts}
    pq.write_table(pa.Table.from_pylist(facts), out / 'streak_facts.parquet', compression='zstd')
    cycle_rows = [{k:v for k,v in f.items() if k not in ('fields','progress')} | {
        'cycle_id':f['fact_id'], 'quality_status':'verified' if f['complete_cycle_eligible'] else 'unknown',
        'formal_eligible':False} for f in facts]
    pq.write_table(pa.Table.from_pylist(cycle_rows), out / 'cycles.parquet', compression='zstd')
    labels = []
    for c in samples:
        f = by_id[c['cycle_id']]
        value = True if c['category']=='confirmed_success_ge5' else False if c['category']=='confirmed_failure_below5' else None
        known = [x['known_at_utc'] for x in f['fields'].values() if x.get('known_at_utc')]
        labels.append(dict(label_id=c['cycle_id']+':reached_5', cycle_id=c['cycle_id'],
            stock_code=c['stock_code'], first_board_date=c['first_board_date'], threshold=5,
            outcome=outcome(value), quality_status='verified' if value is not None and c['outcome_eligible'] else 'unknown',
            quality_scope='local_five_session_outcome_not_whole_market',
            outcome_known_at_utc=max(known) if known else None, label_version=c['label_version'],
            formal_eligible=False, category=c['category']))
    pq.write_table(pa.Table.from_pylist(labels), out / 'labels.parquet', compression='zstd')
    progress = [dict(cycle_id=f['fact_id'], stock_code=f['stock_code'], **p) for f in facts for p in f['progress']]
    pq.write_table(pa.Table.from_pylist(progress), out / 'cycle_days.parquet', compression='zstd')
    features = json.loads((features_dir / 'samples.json').read_text())
    # Feature whitelist deliberately excludes outcomes and outcome-derived selection flags.
    feature_rows = [{k:c[k] for k in ('cycle_id','stock_code','security_id','first_board_date',
        'decision_cutoff_utc','cutoff_exclusive','features','null_reasons','feature_provenance')} for c in features]
    pq.write_table(pa.Table.from_pylist(feature_rows), out / 'preboard_features.parquet', compression='zstd')
    # Source hashes also prove no input changed during construction.
    with Research(root) as r:
        track(root / 'research/catalog.json')
        for relative in r.catalog['datasets']:
            base = root / relative
            manifest = json.loads(track(base / 'manifest.json').read_text())
            for item in manifest['months'].values(): track(base / item['file'])
        db = r.db
        db.execute("SET memory_limit='1GB'")
        db.execute('SET temp_directory=' + literal(out / 'tmp'))
        for p in out.glob('*.parquet'):
            db.read_parquet(str(p)).create_view(p.stem)
        def export(name, sql):
            db.sql(sql).write_parquet(str(out / (name + '.parquet')), compression='zstd')
            db.read_parquet(str(out / (name + '.parquet'))).create_view(name)
        export('day_evidence', """SELECT coalesce(n.stock_code,o.stock_code) AS stock_code,
            coalesce(n.trade_date,o.trade_date) AS trade_date,
            coalesce(n.status,o.status) AS status, coalesce(n.is_st,o.is_st) AS is_st,
            coalesce(n.evidence_ids,o.evidence_ids) AS evidence_ids,
            coalesce(n.known_at_utc,o.known_at_utc) AS known_at_utc,
            o.limit.numeric_limit_price AS numeric_limit_price
            FROM latest_day_evidence n FULL JOIN prior_day_evidence o USING(stock_code,trade_date)""")
        export('daily_supplements', """SELECT * FROM pg_stock_daily WHERE adjustment='none' AND ingestion_status='success'
            QUALIFY row_number() OVER(PARTITION BY stock_code,trade_date ORDER BY
                CASE provider WHEN 'baostock' THEN 0 ELSE 1 END,fetched_at DESC NULLS LAST,id DESC)=1""")
        export('events', """WITH latest AS (SELECT *,dense_rank() OVER(PARTITION BY provider,endpoint,trade_date,limit_type
              ORDER BY fetched_at DESC NULLS LAST,raw_id DESC) AS revision FROM pg_limitup_events WHERE ingestion_status='success')
            SELECT * EXCLUDE(revision), 'event:'||provider||':'||endpoint||':'||stock_code||':'||trade_date||':'||limit_type AS event_id
            FROM latest WHERE revision=1
            QUALIFY row_number() OVER(PARTITION BY provider,endpoint,stock_code,trade_date,limit_type ORDER BY id DESC)=1""")
        export('selected_events', """SELECT * FROM events QUALIFY row_number() OVER(PARTITION BY stock_code,trade_date
            ORDER BY CASE limit_type WHEN 'up' THEN 0 WHEN 'down' THEN 1 ELSE 2 END,
            CASE provider WHEN 'tushare_proxy' THEN 0 ELSE 1 END,fetched_at DESC NULLS LAST,id DESC)=1""")
        export('event_conflicts', """SELECT stock_code,trade_date,count(DISTINCT limit_type)>1 AS status_conflict,
            count(DISTINCT consecutive_limit_days)>1 AS height_conflict FROM events GROUP BY 1,2""")
        export('market_sentiment', """SELECT *, 'sentiment:'||provider||':'||trade_date AS sentiment_id,
            'diagnostic' AS quality_status FROM pg_market_sentiment_daily""")
        db.sql("""SELECT * FROM market_sentiment QUALIFY row_number() OVER(PARTITION BY trade_date
            ORDER BY CASE provider WHEN 'tushare_proxy' THEN 0 ELSE 1 END,calculated_at DESC)=1""").create_view('selected_sentiment')
        # An OHLCV tuple is chosen as a unit: purchased > extension > local BaoStock > local AKShare.
        export('price_candidates', """SELECT stock_code,trade_date,open,high,low,close,volume,amount,pre_close,source,
            NULL::BIGINT AS pg_daily_id, CASE WHEN source LIKE 'purchased_%' THEN 0 ELSE 1 END AS priority FROM raw_daily
            UNION ALL SELECT stock_code,trade_date,open,high,low,close,volume,amount,pre_close,provider,id,
            CASE provider WHEN 'baostock' THEN 2 ELSE 3 END FROM daily_supplements""")
        export('price_conflicts', """SELECT stock_code,trade_date,count(*) AS candidates,
            max(close)-min(close) AS close_spread,
            count(DISTINCT (open,high,low,close))>1 AS ohlc_conflict
            FROM price_candidates GROUP BY 1,2 HAVING count(*)>1""")
        db.sql("""SELECT * FROM price_candidates QUALIFY row_number() OVER(PARTITION BY stock_code,trade_date
            ORDER BY priority,source,pg_daily_id DESC NULLS LAST)=1""").create_view('prices')
        db.sql("""SELECT stock_code,trade_date FROM prices UNION SELECT stock_code,trade_date FROM events
            UNION SELECT stock_code,trade_date FROM day_evidence""").create_view('keys')
        export('resolved_historical_status', """SELECT h.stock_code,c.trade_date,
            CASE WHEN count(DISTINCT (risk_warning_status='none')) FILTER(WHERE risk_warning_status<>'unknown')=1
                THEN bool_or(risk_warning_status<>'none') FILTER(WHERE risk_warning_status<>'unknown') END AS is_st,
            CASE WHEN count(DISTINCT trading_status) FILTER(WHERE trading_status<>'unknown')=1
                THEN bool_or(trading_status='normal_trading') FILTER(WHERE trading_status<>'unknown') END AS trade_status,
            count(DISTINCT (risk_warning_status='none')) FILTER(WHERE risk_warning_status<>'unknown')>1
                OR count(DISTINCT trading_status) FILTER(WHERE trading_status<>'unknown')>1 AS conflict,
            list(DISTINCT h.evidence_id) AS evidence_ids
            FROM historical_stock_status h JOIN calendar c ON c.trade_date BETWEEN h.valid_from AND h.valid_to
            JOIN security_aliases a ON a.stock_code=h.stock_code AND a.security_id=h.security_id
                AND c.trade_date>=a.valid_from AND (a.valid_to IS NULL OR c.trade_date<=a.valid_to)
            WHERE h.verification_status='source_observed' GROUP BY 1,2""")
        # Keep all purpose-specific states; missing PE never invalidates OHLCV.
        sql = f"""SELECT k.stock_code,k.trade_date,year(k.trade_date)::INT AS year,month(k.trade_date)::INT AS month,
            'day:'||k.stock_code||':'||k.trade_date AS stock_day_id,
            p.open,p.high,p.low,p.close,p.volume,p.amount,p.pre_close,p.source AS price_source,
            p.pg_daily_id, v.id AS supplement_pg_id, v.raw_id AS supplement_raw_id,
            coalesce(v.total_market_cap,e.total_market_cap) AS total_market_cap,
            coalesce(v.circulating_market_cap,e.circulating_market_cap) AS circulating_market_cap,
            v.pe_ttm,v.pb,coalesce(v.turnover_rate,e.turnover_rate) AS turnover_rate,
            v.provider AS valuation_source,e.event_id,e.provider AS event_source,e.raw_id AS event_raw_id,
            CASE WHEN ec.status_conflict OR (e.limit_type='up' AND f.status='confirmed_not_limit_up') OR (e.limit_type IN ('down','broken') AND f.status='confirmed_limit_up') THEN NULL WHEN f.status='confirmed_limit_up' OR e.limit_type='up' THEN true
                WHEN f.status='confirmed_not_limit_up' OR e.limit_type IN ('down','broken') THEN false END AS is_limit_up,
            CASE WHEN ec.height_conflict THEN NULL ELSE e.consecutive_limit_days END AS consecutive_limit_days,
            e.first_limit_time,e.last_limit_time,e.open_times,e.seal_amount,
            f.numeric_limit_price AS official_limit_up_price,
            NULL::DOUBLE AS official_limit_down_price,
            CASE WHEN hs.conflict THEN NULL ELSE coalesce(hs.is_st,v.is_st,s.is_st,f.is_st) END AS is_st, CASE WHEN hs.conflict THEN NULL ELSE coalesce(hs.trade_status,v.trade_status,s.trade_status) END AS trade_status, hs.evidence_ids AS historical_status_evidence_ids, coalesce(hs.conflict,false) AS status_conflict,
            CASE WHEN hs.is_st IS NOT NULL OR hs.trade_status IS NOT NULL THEN 'historical_status_evidence' ELSE coalesce(v.provider,s.provider) END AS status_source,
            CASE WHEN ec.status_conflict OR (e.limit_type='up' AND f.status='confirmed_not_limit_up') OR (e.limit_type IN ('down','broken') AND f.status='confirmed_limit_up') THEN 'unknown'
                WHEN f.status IN ('confirmed_limit_up','confirmed_not_limit_up') OR e.event_id IS NOT NULL THEN 'verified'
                ELSE 'unknown' END AS limit_quality,
            CASE WHEN CASE WHEN hs.conflict THEN NULL ELSE coalesce(hs.is_st,v.is_st,s.is_st,f.is_st) END IS NOT NULL THEN 'verified' ELSE 'unknown' END AS st_quality,
            CASE WHEN p.close>0 AND p.open>0 AND p.low>0 AND p.high>=greatest(p.open,p.close,p.low)
                AND p.low<=least(p.open,p.close) AND p.volume>=0 AND p.amount>=0
                AND isfinite(p.close) AND isfinite(p.high) AND isfinite(p.volume) AND isfinite(p.amount)
                THEN CASE WHEN pc.ohlc_conflict THEN 'diagnostic' ELSE 'verified' END
                WHEN p.source IS NOT NULL THEN 'diagnostic' ELSE 'unknown' END AS price_quality,
            CASE WHEN v.pe_ttm IS NOT NULL OR v.pb IS NOT NULL OR coalesce(v.total_market_cap,e.total_market_cap) IS NOT NULL
                THEN 'diagnostic' ELSE 'unknown' END AS valuation_quality,
            m.sentiment_id,m.limit_up_count,m.broken_limit_rate,m.max_consecutive_boards,
            coalesce(m.quality_status,'unknown') AS sentiment_quality,
            f.evidence_ids AS limit_evidence_ids, f.known_at_utc AS limit_known_at_utc,
            e.available_at AS event_available_at, false AS feature_pit_verified,
            coalesce(pc.ohlc_conflict,false) AS price_conflict,
            coalesce(ec.status_conflict,false) OR coalesce(e.limit_type='up' AND f.status='confirmed_not_limit_up',false) OR coalesce(e.limit_type IN ('down','broken') AND f.status='confirmed_limit_up',false) AS event_conflict
            FROM keys k LEFT JOIN prices p USING(stock_code,trade_date)
            LEFT JOIN daily_supplements v USING(stock_code,trade_date)
            LEFT JOIN selected_events e USING(stock_code,trade_date)
            LEFT JOIN event_conflicts ec USING(stock_code,trade_date)
            LEFT JOIN status_evidence s USING(stock_code,trade_date)
            LEFT JOIN resolved_historical_status hs USING(stock_code,trade_date)
            LEFT JOIN day_evidence f USING(stock_code,trade_date)
            LEFT JOIN price_conflicts pc USING(stock_code,trade_date)
            LEFT JOIN selected_sentiment m USING(trade_date)
            WHERE k.trade_date BETWEEN DATE '2017-01-01' AND DATE '{end.isoformat()}'"""
        print('Writing partitioned stock-day dataset', flush=True)
        db.execute(f"COPY ({sql}) TO {literal(out / 'stock_daily')} (FORMAT PARQUET,PARTITION_BY(year,month),COMPRESSION ZSTD)")
        db.read_parquet(str(out / 'stock_daily/**/*.parquet'), hive_partitioning=True).create_view('stock_daily')
        export('first_board_samples', """SELECT 'first:'||stock_code||':'||trade_date AS sample_id,* FROM stock_daily
            WHERE is_limit_up=true AND consecutive_limit_days=1 AND NOT event_conflict""")
        export('sample_cycles', """SELECT s.sample_id,c.cycle_id FROM first_board_samples s JOIN cycles c
            ON s.stock_code=c.stock_code AND s.trade_date=CAST(c.first_board_date AS DATE)""")
        export('sample_labels', """SELECT s.sample_id,t.threshold,
            CASE WHEN count(l.cycle_id)=1 THEN max(l.outcome) ELSE 'unknown' END AS outcome,
            CASE WHEN count(l.cycle_id)=1 THEN max(l.quality_status) ELSE 'unknown' END AS quality_status,
            CASE WHEN count(l.cycle_id)=1 THEN max(l.outcome_known_at_utc) END AS outcome_known_at_utc,
            false AS formal_eligible
            FROM first_board_samples s CROSS JOIN (VALUES (5)) t(threshold)
            LEFT JOIN sample_cycles sc USING(sample_id)
            LEFT JOIN labels l ON l.cycle_id=sc.cycle_id AND l.threshold=t.threshold GROUP BY 1,2""")
        export('yearly_coverage', f"""WITH obs AS (SELECT year,count(*) AS rows,count(close) AS price_rows,
            count(DISTINCT trade_date) FILTER(WHERE close IS NOT NULL) AS observed_days FROM stock_daily GROUP BY 1),
            exp AS (SELECT year(trade_date) AS year,count(*) AS expected_days FROM calendar
                WHERE trade_date BETWEEN DATE '2017-01-01' AND DATE '{end.isoformat()}' GROUP BY 1),
            pairs AS (SELECT year(c.trade_date) AS year,count(*) AS expected_stock_sessions,
                count(d.close) AS matched_stock_sessions FROM calendar c JOIN stocks s
                ON c.trade_date>=s.listing_date AND (s.delisting_date IS NULL OR c.trade_date<=s.delisting_date)
                LEFT JOIN stock_daily d ON d.stock_code=s.stock_code AND d.trade_date=c.trade_date
                WHERE c.trade_date BETWEEN DATE '2017-01-01' AND DATE '{end.isoformat()}' GROUP BY 1)
            SELECT *,observed_days*1.0/expected_days AS date_coverage,
                matched_stock_sessions*1.0/expected_stock_sessions AS known_catalogue_coverage
            FROM exp LEFT JOIN obs USING(year) LEFT JOIN pairs USING(year) ORDER BY year""")
        columns = ['open','high','low','close','volume','amount','pre_close','is_limit_up','consecutive_limit_days',
            'total_market_cap','circulating_market_cap','pe_ttm','pb','turnover_rate','first_limit_time','open_times',
            'seal_amount','official_limit_up_price','official_limit_down_price','is_st','trade_status','limit_up_count']
        summary = {'version': VERSION, 'built_at_utc': stamp, 'data_end': end.isoformat(), 'postgres_snapshot': pg_snapshot,
            'rows': r.query('SELECT count(*) n FROM stock_daily')[0]['n'],
            'yearly': r.query('SELECT * FROM yearly_coverage'),
            'counts': r.query("""SELECT count(*) FILTER(WHERE is_limit_up AND limit_quality='verified') confirmed_limit_up,
                count(*) FILTER(WHERE price_conflict) price_conflicts,count(*) FILTER(WHERE event_conflict) event_conflicts FROM stock_daily""")[0],
            'tables': {n:r.query(f'SELECT count(*) n FROM {n}')[0]['n'] for n in ['events','cycles','streak_facts','first_board_samples','labels','preboard_features','legacy_cycles','pg_stock_themes','cross_qqq']},
            'labels': r.query('SELECT threshold,outcome,quality_status,count(*) n FROM labels GROUP BY ALL ORDER BY 1,2'),
            'sample_labels': r.query('SELECT threshold,outcome,quality_status,count(*) n FROM sample_labels GROUP BY ALL ORDER BY 1,2'),
            'field_coverage': r.query('SELECT '+','.join(f'count({c})*1.0/count(*) AS {c}' for c in columns)+' FROM stock_daily')[0],
            'quality': {c:r.query(f'SELECT {c} AS status,count(*) n FROM stock_daily GROUP BY 1') for c in ['price_quality','limit_quality','st_quality','valuation_quality','sentiment_quality']},
            'exact_observed_cycles': sum(f['complete_cycle_eligible'] for f in facts),
            'formal_whole_market_labels': 0,
            'blockers': ['No certified whole-market event/ST/special-session/official-limit history',
                'Latest cycles cover local event observations only; full-market annual inference remains blocked',
                'Historical download time does not certify point-in-time availability; first-board features are EOD',
                'Adjustment factors not certified; no formal total-return/backtest use',
                'No independent A-share index price dataset found; QQQ is preserved separately',
                f'Historical theme relationships absent; BSE out of scope; data ends {end}',
                'Known lifecycle catalogue coverage is not proof of full-market completeness']}
        if r.query('SELECT count(*) n FROM (SELECT stock_code,trade_date FROM stock_daily GROUP BY ALL HAVING count(*)>1)')[0]['n']:
            raise ValueError('Duplicate normalized primary key')
        if r.query("SELECT count(*) n FROM labels WHERE outcome='failure' AND quality_status='unknown'")[0]['n']:
            raise ValueError('Unknown mapped to failure')
        dump(out / 'summary.json', summary)
    # A persistent DuckDB catalogue reads immutable Parquet only, without a PG extension or network.
    with duckdb.connect(str(out / 'research.duckdb')) as db:
        for p in out.glob('*.parquet'):
            db.execute(f'CREATE VIEW "{p.stem}" AS SELECT * FROM read_parquet({literal(p)})')
        db.execute(f"CREATE VIEW stock_daily AS SELECT * FROM read_parquet({literal(out / 'stock_daily/**/*.parquet')},hive_partitioning=true)")
    for name, digest in inputs.items():
        if sha(Path(name)) != digest: raise ValueError('Input changed during release: '+name)
    dump(out / 'inputs.json', inputs)
    shutil.rmtree(out / 'tmp', ignore_errors=True)
    files = {str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file()}
    manifest = dict(version=VERSION,dataset=str(out.relative_to(target)),files=files)
    dump(out / 'manifest.json', manifest)
    dump(target / 'manifest.tmp', manifest)
    (target / 'manifest.tmp').replace(target / 'manifest.json')
    print(json.dumps({'snapshot': str(out), 'rows': summary['rows'], 'counts': summary['counts'], 'tables': summary['tables']},ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--end', type=date.fromisoformat, help='截止交易日；默认保留当前快照截止日')
    args = parser.parse_args()
    lock = args.root / 'research/unified/build.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        build(args.root,args.end)
