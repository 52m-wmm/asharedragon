"""Read-only ZIP ingestion into isolated monthly Parquet staging; never writes PostgreSQL.

Run with the project's .venv. Staging is NOT an approved research input. A separate
validation/release step must establish adjustment, units and market coverage.
"""
import argparse
from collections import defaultdict
from datetime import datetime
import fcntl
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from zipfile import ZipFile

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

VERSION = 1
FIELDS = ('stock_code', 'trade_date', 'open', 'high', 'low', 'close', 'volume',
          'amount', 'source', 'adjustment_type', 'pre_close', 'adj_factor', 'vwap')


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')
    temporary.replace(path)


def member_date(info):
    p = PurePosixPath(info.filename)
    if p.is_absolute() or '..' in p.parts or '\\' in info.filename:
        raise ValueError('Unsafe ZIP path')
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ValueError('ZIP symlink rejected')
    if info.is_dir():
        return None
    if not re.fullmatch(r'1d_price/\d{8}\.parquet', info.filename):
        raise ValueError(f'Unrecognized member: {info.filename}')
    if info.file_size > 32 * 1024 * 1024:
        raise ValueError('Member exceeds bounded daily input size')
    return datetime.strptime(p.stem, '%Y%m%d').date()


def normalize(table, expected, source, connection):
    required = {'code', 'date', 'open', 'high', 'low', 'close', 'vol', 'amount',
                'pre_close', 'adj_factor', 'vwap'}
    if not required <= set(table.column_names):
        raise ValueError('Required source fields missing; no values fabricated')
    if not pa.types.is_string(table.schema.field('code').type):
        raise ValueError('Stock code must be text to retain leading zeros')
    connection.register('input_daily', table)
    dates = connection.execute("SELECT DISTINCT try_strptime(date, '%Y%m%d')::DATE FROM input_daily").fetchall()
    if dates != [(expected,)]:
        raise ValueError(f'Internal dates disagree with member date: {dates}')
    # Explicit exchange/prefix checks keep Shanghai indexes, ETFs and B shares out.
    invalid = connection.execute("""SELECT count(*) FROM input_daily WHERE code IS NULL OR
        NOT regexp_full_match(code, '(60[0-9]{4}|688[0-9]{3}|689[0-9]{3})\\.SH|(00[0-9]{4}|30[0-9]{4})\\.SZ|[489][0-9]{5}\\.BJ')""").fetchall()[0][0]
    if invalid:
        raise ValueError(f'{invalid} unknown/non-A-share codes; pause batch for classification')
    return connection.sql("""SELECT code AS stock_code, strptime(date, '%Y%m%d')::DATE AS trade_date,
        open, high, low, close, vol AS volume, amount, ? AS source,
        'unknown' AS adjustment_type, pre_close, adj_factor, vwap FROM input_daily""", params=[source]).to_arrow_table()


def quality(connection, path):
    connection.read_parquet(str(path)).create_view('batch', replace=True)
    n = connection.execute('SELECT count(*) FROM batch').fetchall()[0][0]
    duplicates = connection.execute('SELECT count(*) FROM (SELECT stock_code,trade_date FROM batch GROUP BY ALL HAVING count(*)>1)').fetchall()[0][0]
    bad = connection.execute('''SELECT count(*) FROM batch WHERE
        NOT isfinite(open) OR NOT isfinite(high) OR NOT isfinite(low) OR NOT isfinite(close)
        OR open<=0 OR high<=0 OR low<=0 OR close<=0
        OR high<greatest(open,close,low) OR low>least(open,close)
        OR volume<0 OR amount<0 OR NOT isfinite(volume) OR NOT isfinite(amount)''').fetchall()[0][0]
    nulls = dict(zip(FIELDS, connection.execute('SELECT ' + ','.join(f'count(*) FILTER (WHERE "{c}" IS NULL)' for c in FIELDS) + ' FROM batch').fetchall()[0]))
    return {'rows': n, 'duplicate_keys': duplicates, 'invalid_ohlcv_rows': bad, 'nulls': nulls}


def run(archive, root, start, end):
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.import.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fingerprint = digest(archive)
        source = 'purchased_usb_' + fingerprint[:12]
        destination = root / source
        destination.mkdir(exist_ok=True)
        progress_path = destination / 'manifest.json'
        progress = json.loads(progress_path.read_text()) if progress_path.exists() else {
            'version': VERSION, 'archive': str(archive), 'sha256': fingerprint,
            'source': source, 'status': 'staging_unverified', 'start': str(start), 'end': str(end), 'months': {}}
        if (progress['version'], progress['start'], progress['end']) != (VERSION, str(start), str(end)):
            raise ValueError('Resume parameters differ from original import')
        connection = duckdb.connect(config={'memory_limit': '512MB', 'threads': '2'})
        with ZipFile(archive) as z:
            groups = defaultdict(list)
            all_dates = []
            infos = z.infolist()
            if len({i.filename for i in infos}) != len(infos):
                raise ValueError('Duplicate ZIP member names')
            for info in infos:
                d = member_date(info)
                if d:
                    all_dates.append(d)
                    if start <= d <= end:
                        groups[d.strftime('%Y-%m')].append((d, info))
            progress['inventory'] = {'entries': len(infos), 'compressed_bytes': archive.stat().st_size,
                'uncompressed_bytes': sum(i.file_size for i in infos), 'filename_min': str(min(all_dates)),
                'filename_max': str(max(all_dates)), 'selected_members': sum(map(len, groups.values())),
                'selected_uncompressed_bytes': sum(i.file_size for g in groups.values() for _, i in g),
                'members': [{'name': i.filename, 'bytes': i.file_size, 'compressed_bytes': i.compress_size, 'crc': i.CRC} for i in infos]}
            save_json(progress_path, progress)
            for month, members in sorted(groups.items()):
                old = progress['months'].get(month, {})
                output = destination / f'year={month[:4]}' / f'month={month[5:]}' / 'daily.parquet'
                if old.get('status') == 'staged' and output.exists() and digest(output) == old.get('sha256'):
                    print(month, 'resume: verified, skipped', flush=True)
                    continue
                if shutil.disk_usage(root).free < max(2 * 1024**3, sum(i.file_size for _, i in members) * 4):
                    raise RuntimeError('Insufficient free space; retained previous completed batches')
                output.parent.mkdir(parents=True, exist_ok=True)
                temporary = output.with_suffix('.parquet.partial')
                temporary.unlink(missing_ok=True)
                writer = None
                try:
                    for d, info in sorted(members, key=lambda pair: pair[0]):
                        # No extract()/extractall(): archive paths never become filesystem paths.
                        table = pq.read_table(pa.BufferReader(z.read(info)))  # ZIP CRC checked on read
                        normalized = normalize(table, d, source, connection)
                        if writer is None:
                            writer = pq.ParquetWriter(temporary, normalized.schema, compression='zstd')
                        writer.write_table(normalized)
                    writer.close()
                    writer = None
                    stats = quality(connection, temporary)
                    if stats['duplicate_keys'] or stats['invalid_ohlcv_rows'] or any(stats['nulls'][c] for c in FIELDS[:8]):
                        raise ValueError('Batch failed quality gate: ' + json.dumps(stats))
                    temporary.replace(output)
                    progress['months'][month] = dict(status='staged', **stats, bytes=output.stat().st_size,
                        sha256=digest(output), files=len(members), path=str(output))
                    print(month, stats['rows'], 'staged', flush=True)
                except Exception as exc:
                    progress['months'][month] = {'status': 'paused', 'error': str(exc)}
                    print(month, 'PAUSED', str(exc), flush=True)
                finally:
                    if writer is not None:
                        writer.close()
                    temporary.unlink(missing_ok=True)
                    save_json(progress_path, progress)
        connection.close()
        print('manifest:', progress_path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--root', type=Path, default=Path.home() / 'Datasets/asharedragon/staging/stocks/daily')
    parser.add_argument('--start', type=datetime.fromisoformat, default=datetime(2017, 1, 1))
    parser.add_argument('--end', type=datetime.fromisoformat, required=True)
    args = parser.parse_args()
    run(args.archive.resolve(), args.root.expanduser().resolve(), args.start.date(), args.end.date())
