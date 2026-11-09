"""Publish this audited purchased source with empirical unit/adjustment evidence.

No exchange-completeness claim; does not write PostgreSQL or reconstruct limit-ups.
"""
import argparse
import fcntl
import json
from pathlib import Path
import shutil

import duckdb
from import_historical_daily import digest, save_json


def release(staging, parent):
    manifest = json.loads((staging/'manifest.json').read_text())
    audit = json.loads((staging/'audit.json').read_text())
    if audit['archive_sha256'] != manifest['sha256'] or audit['paused_months']:
        raise ValueError('Incomplete or mismatched audit')
    if audit['unexpected_dates'] or audit['duplicate_keys'][0]['keys'] or audit['price_precision'][0]['off_tick']:
        raise ValueError('Invalid dates, duplicates or price precision')
    comparisons = [r for r in audit['baostock_akshare_comparison'] if r['provider']=='baostock' and r['adjustment']=='none']
    if sum(r['overlaps'] for r in comparisons)<1000 or any(r['price_differences'] or r['volume_differences'] or r['amount_differences'] for r in comparisons):
        raise ValueError('Insufficient consistent unadjusted/share/CNY local evidence')
    if not audit['tushare_comparison'] or any(r['close_differences'] or r['amount_differences'] for r in audit['tushare_comparison']):
        raise ValueError('Tushare evidence missing or inconsistent')
    if any(r['inconsistent_vwap'] or r['vwap_outside_ohlc'] for r in audit['vwap_consistency']):
        raise ValueError('Price/volume/amount relationship inconsistent')
    if audit.get('partition_hashes') != {k:v.get('sha256') for k,v in manifest['months'].items()} or len(manifest['months']) != audit['file_count']:
        raise ValueError('Audit does not cover all partitions')
    destination = parent / manifest['source']
    destination.mkdir(parents=True,exist_ok=True)
    with (destination/'.release.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        path = destination/'manifest.json'
        result = json.loads(path.read_text()) if path.exists() else {
            'status':'publishing', 'source':manifest['source'], 'archive_sha256':manifest['sha256'],
            'adjustment_type':'none','volume_unit':'shares','amount_unit':'CNY',
            'evidence_basis':'empirical comparison with local unadjusted BaoStock and Tushare; vendor documentation absent',
            'coverage':'partial: SH/SZ only, source ends 2026-03-13; absent bars are not inferred suspensions',
            'research_limitations':['No historical ST/IPO/resumption status', 'Do not derive limit-up prices from previous close alone',
                'adj_factor is retained but its corporate-action completeness is not certified', 'No 2026-03-16 onward or Beijing coverage'],
            'months':{}}
        if result['archive_sha256'] != manifest['sha256']:
            raise ValueError('Destination belongs to a different source')
        db = duckdb.connect(config={'memory_limit':'512MB','threads':'2'})
        for month, info in sorted(manifest['months'].items()):
            if info['status'] != 'staged' or digest(Path(info['path'])) != info['sha256']:
                raise ValueError('Staging partition changed after validation')
            if info['duplicate_keys'] or info['invalid_ohlcv_rows'] or any(info['nulls'][c] for c in ('stock_code','trade_date','open','high','low','close','volume','amount')):
                raise ValueError('Partition fails quality gate')
            relative = f'year={month[:4]}/month={month[5:]}/daily.parquet'
            output = destination/relative
            previous = result['months'].get(month)
            if previous and output.exists() and digest(output)==previous['sha256']:
                continue
            if shutil.disk_usage(destination).free < 1024**3:
                raise RuntimeError('Insufficient space')
            output.parent.mkdir(parents=True,exist_ok=True)
            temporary=output.with_suffix('.parquet.partial')
            temporary.unlink(missing_ok=True)
            try:
                db.read_parquet(info['path'], hive_partitioning=False).create_view('staged',replace=True)
                db.sql("SELECT * REPLACE ('none' AS adjustment_type) FROM staged ORDER BY stock_code,trade_date").write_parquet(str(temporary), compression='zstd', row_group_size=100000)
                count=db.read_parquet(str(temporary)).count('*').fetchall()[0][0]
                if count!=info['rows']:
                    raise ValueError('Release row count changed')
                temporary.replace(output)
                result['months'][month]={'file':relative,'rows':count,'bytes':output.stat().st_size,'sha256':digest(output)}
                result['status']='publishing'
                save_json(path,result)
            finally:
                temporary.unlink(missing_ok=True)
        result['status']='validated_partial'
        save_json(destination/'audit.json',audit)
        save_json(path,result)
        db.close()
    print(destination)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('staging',type=Path)
    p.add_argument('--root',type=Path,default=Path.home()/'Datasets/asharedragon/processed/stocks/daily')
    a=p.parse_args()
    release(a.staging,a.root)
