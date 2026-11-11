"""Offline source-selection candidate over a pinned U; never writes an active pointer."""
import argparse
import json
from pathlib import Path
import duckdb
from scripts.finalize_research import select_daily_prices, SOURCE_VERSION, dump, sha, literal


def build(parent, output):
    parent, output = Path(parent).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Output must be a new directory; existing snapshots are immutable')
    manifest = json.loads((parent/'manifest.json').read_text())
    inputs = {str(parent/'manifest.json'): sha(parent/'manifest.json')}
    for relative, expected in manifest['files'].items():
        p = (parent/relative).resolve()
        if not p.is_relative_to(parent) or sha(p) != expected:
            raise ValueError('Parent file hash mismatch: '+relative)
        inputs[str(p)] = expected
    for script in (Path(__file__), Path(__file__).with_name('finalize_research.py')):
        inputs[str(script.resolve())] = sha(script)
    output.mkdir(parents=True)
    with duckdb.connect(config={'threads':2,'memory_limit':'1GB'}) as db:
        db.execute('SET temp_directory='+literal(output/'tmp'))
        db.read_parquet(str(parent/'price_candidates.parquet')).create_view('old_candidates')
        # Frozen non-PG normalized inputs. All PG revisions are taken from the
        # parent archive, not from its prematurely selected daily_supplements.
        db.sql('SELECT * FROM old_candidates WHERE pg_daily_id IS NULL').create_view('raw_daily')
        db.read_parquet(str(parent/'pg_stock_daily.parquet')).create_view('pg_stock_daily')
        db.read_parquet(str(parent/'stock_daily/**/*.parquet'),hive_partitioning=True).create_view('old_daily')
        def export(name, sql):
            db.sql(sql).write_parquet(str(output/(name+'.parquet')),compression='zstd')
            db.read_parquet(str(output/(name+'.parquet'))).create_view(name)
        def rows(sql):
            q=db.execute(sql);keys=[x[0] for x in q.description]
            return [dict(zip(keys,r)) for r in q.fetchall()]
        select_daily_prices(db,export)
        export('selected_prices','SELECT * FROM prices ORDER BY stock_code,trade_date')
        # Only daily price/source fields change. Metadata/ST/events remain pinned
        # to the parent and cannot be upgraded by this source-selection repair.
        sql="""SELECT d.* REPLACE(p.open AS open,p.high AS high,p.low AS low,p.close AS close,
          p.volume AS volume,p.amount AS amount,p.pre_close AS pre_close,p.source AS price_source,
          p.pg_daily_id AS pg_daily_id,
          CASE WHEN p.source IS NULL THEN 'unknown' ELSE 'verified' END AS price_quality,
          coalesce(c.ohlc_conflict OR c.reference_conflict,false) AS price_conflict),
          coalesce(c.reference_conflict,false) reference_conflict,
          CASE WHEN c.ohlc_conflict OR c.reference_conflict THEN 'conflict_quarantined'
            WHEN c.candidates IS NULL THEN 'no_price_candidate'
            WHEN p.source IS NULL THEN 'no_valid_candidate'
            ELSE p.candidate_reason END source_selection_reason
          FROM old_daily d LEFT JOIN selected_prices p USING(stock_code,trade_date)
          LEFT JOIN price_conflicts c USING(stock_code,trade_date)"""
        db.execute(f"COPY ({sql}) TO {literal(output/'stock_daily')} (FORMAT PARQUET, PARTITION_BY(year,month), COMPRESSION ZSTD)")
        db.read_parquet(str(output/'stock_daily/**/*.parquet'),hive_partitioning=True).create_view('new_daily')
        export('selection_diff',"""SELECT d.stock_code,d.trade_date,d.price_source old_source,n.price_source new_source,
          d.pg_daily_id old_pg_id,n.pg_daily_id new_pg_id,
          d.open old_open,n.open new_open,d.high old_high,n.high new_high,
          d.low old_low,n.low new_low,d.close old_close,n.close new_close,
          d.pre_close old_pre_close,n.pre_close new_pre_close,
          d.volume old_volume,n.volume new_volume,d.amount old_amount,n.amount new_amount,
          d.price_quality old_quality,n.price_quality new_quality,
          d.price_conflict old_conflict,n.price_conflict new_conflict,n.source_selection_reason,
          d.price_source IS DISTINCT FROM n.price_source source_changed,
          (d.open,d.high,d.low,d.close) IS DISTINCT FROM (n.open,n.high,n.low,n.close) ohlc_changed,
          d.pre_close IS DISTINCT FROM n.pre_close reference_changed
          FROM old_daily d JOIN new_daily n USING(stock_code,trade_date)
          WHERE (d.price_source,d.pg_daily_id,d.open,d.high,d.low,d.close,d.volume,d.amount,d.pre_close,d.price_quality,d.price_conflict)
             IS DISTINCT FROM (n.price_source,n.pg_daily_id,n.open,n.high,n.low,n.close,n.volume,n.amount,n.pre_close,n.price_quality,n.price_conflict)
          ORDER BY d.trade_date,d.stock_code""")
        export('conflict_evidence',"""SELECT p.*,c.ohlc_conflict,c.reference_conflict,c.volume_conflict,c.amount_conflict
          FROM price_candidates p JOIN price_conflicts c USING(stock_code,trade_date)
          WHERE c.ohlc_conflict OR c.reference_conflict OR c.volume_conflict OR c.amount_conflict
          ORDER BY trade_date,stock_code,source,pg_daily_id""")
        # All PG overlap, including the former BaoStock preference case.
        overlap=rows("""SELECT stock_code,trade_date,count(DISTINCT provider) providers
          FROM pg_price_candidates GROUP BY 1,2 HAVING providers>1 ORDER BY 2,1""")
        changes=rows('SELECT * FROM selection_diff ORDER BY trade_date,stock_code')
        boundaries=rows("""SELECT p.*,c.ohlc_conflict,c.reference_conflict,
          s.source selected_source,s.pg_daily_id selected_pg_id
          FROM price_candidates p JOIN price_conflicts c USING(stock_code,trade_date)
          LEFT JOIN selected_prices s USING(stock_code,trade_date)
          WHERE p.trade_date IN ('2026-03-13','2026-03-16','2026-11-06','2026-11-09','2026-11-10')
            AND p.stock_code IN ('000001.SZ','600537.SH','300750.SZ','688001.SH') ORDER BY 2,1,source""")
        summary={'rule_version':SOURCE_VERSION,'parent':str(parent),'status':'candidate',
          'scope':'source-selection repair only; no D2/reference eligibility or aggregates promoted',
          'counts':rows("""SELECT (SELECT count(*) FROM old_daily) old_rows,
            (SELECT count(*) FROM new_daily) new_rows,
            (SELECT count(*) FROM old_candidates) old_candidates,
            (SELECT count(*) FROM price_candidates) new_candidates,
            (SELECT count(*) FROM pg_price_candidates) compatible_latest_pg_candidates,
            (SELECT count(*) FROM old_daily WHERE price_conflict) old_price_conflicts,
            count(*) FILTER(WHERE ohlc_conflict) new_ohlc_conflicts,
            count(*) FILTER(WHERE reference_conflict) new_reference_conflicts,
            count(*) FILTER(WHERE ohlc_conflict OR reference_conflict) quarantined_price_conflicts,
            count(*) FILTER(WHERE volume_conflict) volume_differences,
            count(*) FILTER(WHERE amount_conflict) amount_differences FROM price_conflicts""")[0],
          'changes':rows("""SELECT count(*) affected_stock_days,count(*) FILTER(WHERE source_changed) source_changes,
            count(*) FILTER(WHERE source_changed AND old_source IS NOT NULL AND new_source IS NOT NULL) provider_reselections,
            count(*) FILTER(WHERE ohlc_changed) ohlc_changes_including_null,
            count(*) FILTER(WHERE ohlc_changed AND new_close IS NOT NULL) nonnull_ohlc_changes,
            count(*) FILTER(WHERE reference_changed) reference_changes_including_null,
            count(*) FILTER(WHERE reference_changed AND new_pre_close IS NOT NULL) nonnull_reference_changes
            FROM selection_diff""")[0],
          'source_before':rows('SELECT price_source,count(*) n FROM old_daily GROUP BY 1 ORDER BY 1'),
          'source_after':rows('SELECT price_source,count(*) n FROM new_daily GROUP BY 1 ORDER BY 1'),
          'pg_overlap':overlap}
        if rows('SELECT stock_code,trade_date FROM new_daily GROUP BY 1,2 HAVING count(*)>1'):
            raise ValueError('Duplicate stock-day')
        assert summary['counts']['old_rows']==summary['counts']['new_rows']
        assert not rows('SELECT * FROM selected_prices JOIN price_conflicts USING(stock_code,trade_date) WHERE ohlc_conflict OR reference_conflict')
        dump(output/'summary.json',summary);dump(output/'selection_diff.json',changes);dump(output/'boundary_samples.json',boundaries)
    for name,digest in inputs.items():
        if sha(Path(name))!=digest:raise ValueError('Input changed: '+name)
    import shutil
    shutil.rmtree(output/'tmp',ignore_errors=True)
    dump(output/'inputs.json',inputs)
    files={str(p.relative_to(output)):sha(p) for p in sorted(output.rglob('*')) if p.is_file()}
    dump(output/'manifest.json',dict(rule_version=SOURCE_VERSION,status='candidate',parent_snapshot=parent.name,
      kind='unified_daily_source_repair',active_changed=False,files=files,
      limitations=['Reference checks here are internal validity, not same-day economic validation',
        'Volume/amount disagreements retained as diagnostics; no volume/amount eligibility granted',
        'ST, lifecycle, events and all aggregates remain parent-version evidence']))
    print(json.dumps(summary,ensure_ascii=False,default=str),flush=True)
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--parent',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();build(a.parent,a.output)
