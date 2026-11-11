"""Build a separate, resumable breadth release from frozen local evidence only."""
import argparse
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path

import duckdb
import pyarrow as pa

from app.research.queries import DEFAULT_ROOT
from app.statistics.market_breadth import VERSION, SCOPE, classification_sql, daily_sql, validate_daily
from app.statistics.reference_recovery import SOURCE

REPO = Path(__file__).resolve().parents[1]


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                                    default=str, allow_nan=False) + '\n')
    temporary.replace(path)


def rows(db, sql, args=None):
    cursor = db.execute(sql, args or [])
    return [dict(zip([c[0] for c in cursor.description], row)) for row in cursor.fetchall()]


# Explicit candidate selectors let the existing builders install into final
# snapshot directories without touching any production active manifest.
_CANDIDATE_MANIFESTS = ContextVar('candidate_manifests', default=None)


def manifest_path(base):
    base = Path(base).resolve()
    mapping = _CANDIDATE_MANIFESTS.get()
    if mapping is None:
        return base / 'manifest.json'
    if base not in mapping:
        raise ValueError('Unregistered candidate manifest base: '+str(base))
    return mapping[base]


@contextmanager
def candidate_manifests(mapping):
    resolved = {Path(k).resolve(): Path(v).resolve() for k, v in mapping.items()}
    if any(path == base/'manifest.json' for base, path in resolved.items()):
        raise ValueError('Candidate selectors cannot be active manifests')
    token = _CANDIDATE_MANIFESTS.set(resolved)
    try:
        yield
    finally:
        _CANDIDATE_MANIFESTS.reset(token)


def snapshot(base):
    manifest = json.loads(manifest_path(base).read_text())
    path = (base / manifest['dataset']).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError('Snapshot path outside its root')
    return path, manifest


def open_inputs(source, recovery, lifecycle):
    db = duckdb.connect(config={'threads': 2, 'memory_limit': '1GB'})
    db.execute('ATTACH ? AS src (READ_ONLY)', [str(source / 'research.duckdb')])
    db.read_parquet(str(recovery)).create_view('recovered_refs')
    db.read_parquet(str(source / 'pg_stock_daily.parquet')).create_view('pg_daily')
    schema = pa.schema([('stock_code', pa.string()), ('status', pa.string())])
    db.register('lifecycle', pa.Table.from_pylist(
        [{'stock_code': r['stock_code'], 'status': r['status']} for r in lifecycle['rows']], schema=schema))
    for name, key in [('src.stock_daily', 'stock_code,trade_date'), ('recovered_refs', 'stock_code,trade_date'),
                      ('src.calendar', 'trade_date'), ('lifecycle', 'stock_code'), ('pg_daily', 'id')]:
        if db.execute(f'SELECT count(*)-count(DISTINCT ({key})) FROM {name}').fetchone()[0]:
            db.close()
            raise ValueError(f'Duplicate input keys: {name}')
    db.execute('CREATE TEMP TABLE cal AS SELECT trade_date,lag(trade_date) OVER(ORDER BY trade_date) prev FROM src.calendar')
    return db


def calculate(db, start, end):
    # The preceding session comes from the full calendar, even at a month/year boundary.
    db.execute('CREATE OR REPLACE TEMP TABLE calendar_slice AS SELECT trade_date FROM cal WHERE trade_date BETWEEN ? AND ?', [start, end])
    db.execute("""CREATE OR REPLACE TEMP TABLE input AS
      SELECT d.*,p.close prior_close,
        (p.price_quality='verified' AND NOT p.price_conflict AND p.trade_status IS DISTINCT FROM false) prior_price_verified,
        r.recovered_reference,
        coalesce(g.adjustment='none' AND g.ingestion_status='success'
          AND g.provider=d.price_source AND g.stock_code=d.stock_code AND g.trade_date=d.trade_date
          AND g.pre_close=d.pre_close AND g.close=d.close AND g.open=d.open
          AND g.high=d.high AND g.low=d.low AND g.pre_close>0 AND isfinite(g.pct_change)
          AND abs((g.close/g.pre_close-1)*100-g.pct_change)<=0.0051,false) pg_reference_verified,
        coalesce(l.status,'missing_evidence') lifecycle_status
      FROM src.stock_daily d JOIN calendar_slice c USING(trade_date)
      JOIN cal ON cal.trade_date=d.trade_date
      LEFT JOIN src.stock_daily p ON p.stock_code=d.stock_code AND p.trade_date=cal.prev
      LEFT JOIN recovered_refs r ON r.stock_code=d.stock_code AND r.trade_date::DATE=d.trade_date
        AND d.price_source=? AND d.price_quality='verified' AND d.pre_close IS NULL
        AND abs(d.close-r.raw_close)<1e-8 AND abs(d.open-r.raw_open)<1e-8
        AND abs(d.high-r.raw_high)<1e-8 AND abs(d.low-r.raw_low)<1e-8
      LEFT JOIN pg_daily g ON g.id=d.pg_daily_id
      LEFT JOIN lifecycle l ON l.stock_code=d.stock_code
      """, [SOURCE])
    db.execute('CREATE OR REPLACE TEMP TABLE evaluated AS ' + classification_sql())
    daily = rows(db, daily_sql())
    validate_daily(daily)
    quality = rows(db, """SELECT trade_date,price_source,reference_basis,price_reason,
      identity_status,lifecycle_status,count(*) records FROM evaluated
      GROUP BY ALL ORDER BY ALL""")
    return daily, quality


def build(root=DEFAULT_ROOT, lifecycle_path=None, start=date(2017, 1, 1), end=None):
    root = Path(root).resolve()
    source, manifest = snapshot(root / 'research/unified')
    history, _ = snapshot(root / 'statistics/history_limits')
    history_summary = json.loads((history / 'summary.json').read_text())
    if Path(history_summary['source_snapshot']).resolve() != source:
        raise ValueError('Reference recovery and unified snapshot do not match')
    if history_summary['reference_recovery']['source'] != SOURCE:
        raise ValueError('Unsupported reference recovery source')
    # Validate frozen inputs, not only the manifest name.
    for filename, expected in manifest['files'].items():
        path = (source / filename).resolve()
        if not path.is_relative_to(source) or sha(path) != expected:
            raise ValueError(f'Input hash mismatch: {filename}')
    recovery = history / 'recovered_references.parquet'
    lifecycle_path = Path(lifecycle_path).resolve()
    lifecycle = json.loads(lifecycle_path.read_text())
    if lifecycle['catalogue_sha256'] != sha(source / 'stocks.parquet'):
        raise ValueError('Lifecycle audit refers to another catalogue')
    actual_counts = {status: sum(r['status'] == status for r in lifecycle['rows'])
                     for status in lifecycle['counts']}
    if actual_counts != lifecycle['counts']:
        raise ValueError('Lifecycle report counts do not match its rows')
    source_end = date.fromisoformat(json.loads((source / 'summary.json').read_text())['data_end'])
    end = end or source_end
    if start > end or end > source_end or start < date(2017, 1, 1):
        raise ValueError('Invalid breadth range')
    inputs = dict(source_snapshot=str(source),source_manifest_sha256=sha(manifest_path(root/'research/unified')),
                  history_snapshot=str(history),history_summary_sha256=sha(history/'summary.json'),
                  recovered_references_sha256=sha(recovery),lifecycle_report=str(lifecycle_path),
                  lifecycle_sha256=sha(lifecycle_path),start=str(start),end=str(end),version=VERSION,
                  code_sha256={str(p.relative_to(REPO)): sha(p) for p in
                    [Path(__file__).resolve(),REPO/'backend/app/statistics/market_breadth.py',
                     REPO/'backend/app/statistics/reference_recovery.py']})
    key = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:20]
    target = root / 'statistics/market_breadth'
    out = target / 'snapshots' / key
    out.mkdir(parents=True, exist_ok=True)
    with (target / 'build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        save(out / 'inputs.json', inputs)
        # Keep every conflict, including stocks absent from the requested date range.
        save(out / 'lifecycle_quality.json', dict(source=str(lifecycle_path),sha256=sha(lifecycle_path),
             counts=lifecycle['counts'],unresolved=[r for r in lifecycle['rows'] if r['status']!='verified'],
             policy='annotation_only; never delete conflict stocks or change denominators'))
        db = open_inputs(source, recovery, lifecycle)
        try:
            months = rows(db, """SELECT strftime(trade_date,'%Y-%m') AS month_key,min(trade_date) AS first_date,
              max(trade_date) AS last_date FROM cal WHERE trade_date BETWEEN ? AND ? GROUP BY 1 ORDER BY 1""", [start,end])
            if not months:
                raise ValueError('No calendar sessions in requested range')
            checksums = {}; reused = 0
            for month in months:
                name = month['month_key']
                parquet = out / (name + '.parquet')
                quality_path = out / (name + '.quality.json')
                checkpoint = out / (name + '.complete.json')
                if checkpoint.exists():
                    saved = json.loads(checkpoint.read_text())
                    if saved == {parquet.name: sha(parquet),quality_path.name: sha(quality_path)}:
                        checksums.update(saved); reused += 1; continue
                    raise ValueError(f'Corrupt completed breadth partition: {name}')
                daily, quality = calculate(db, month['first_date'], month['last_date'])
                # SQL retains a stable schema even when every value is null.
                db.execute('CREATE OR REPLACE TEMP TABLE daily_result AS ' + daily_sql())
                temporary = parquet.with_suffix('.parquet.tmp')
                db.execute('COPY daily_result TO ? (FORMAT PARQUET, COMPRESSION ZSTD)', [str(temporary)])
                temporary.replace(parquet)
                save(quality_path, quality)
                saved = {parquet.name: sha(parquet),quality_path.name: sha(quality_path)}
                save(checkpoint, saved); checksums.update(saved)
                print(f'{name}: {len(daily)} sessions', flush=True)
            db.read_parquet([str(out/(m['month_key']+'.parquet')) for m in months]).create_view('breadth')
            total = rows(db, """SELECT count(*) sessions,min(trade_date) AS first_date,max(trade_date) AS last_date,
              sum(observed_count) observed_count,sum(price_valid_count) price_valid_count,
              sum(price_unknown_count) price_unknown_count,
              sum(lifecycle_conflict_observed_count) lifecycle_conflict_observations,
              sum(lifecycle_conflict_valid_count) lifecycle_conflict_valid_observations FROM breadth""")[0]
            validate_daily(rows(db, 'SELECT * FROM breadth'))
            if sha(recovery)!=inputs['recovered_references_sha256'] or sha(lifecycle_path)!=inputs['lifecycle_sha256']:
                raise ValueError('Input changed during build')
            summary_path = out/'summary.json'
            if not summary_path.exists():
                save(summary_path,dict(version=VERSION,snapshot_id=key,scope=SCOPE,totals=total,
                    computed_at_utc=datetime.now(timezone.utc).isoformat(),source_snapshot=str(source),
                    coverage_ratio=None,universe_status='unknown',lifecycle_counts=lifecycle['counts'],
                    reference_policy='purchased supplied reference corroborated by preceding SSE close; same-response checked references; no lag fill',
                    promotion_policy='14/45 and 4/8 are supplier diagnostics only; strict non-ST rates remain null until identity verified; not computed in E1',
                    formal_research_eligible=False,files=checksums))
            else:
                saved_summary=json.loads(summary_path.read_text())
                if saved_summary['files']!=checksums:
                    raise ValueError('Completed snapshot manifest differs from partitions')
            save(manifest_path(target),dict(version=VERSION,dataset=str(out.relative_to(target)),
                                           summary_sha256=sha(summary_path)))
            return dict(snapshot=str(out),months=len(months),reused_months=reused,totals=total)
        finally:
            db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    parser.add_argument('--lifecycle-report',type=Path,required=True)
    parser.add_argument('--start',type=date.fromisoformat,default=date(2017,1,1))
    parser.add_argument('--end',type=date.fromisoformat)
    args=parser.parse_args()
    print(json.dumps(build(args.root,args.lifecycle_report,args.start,args.end),ensure_ascii=False,default=str))
