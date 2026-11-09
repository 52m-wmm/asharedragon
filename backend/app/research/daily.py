"""Open one audited source without blending or replacing other providers."""
import json
from pathlib import Path

import duckdb


def connect_daily(dataset: Path):
    """Return an in-memory DuckDB connection exposing a source-specific `daily` view.

    Caller owns/should close the connection. Missing sessions are absent, not zero.
    This dataset alone does not establish historical ST status or limit-up prices.
    """
    dataset = Path(dataset).expanduser().resolve()
    manifest = json.loads((dataset / 'manifest.json').read_text())
    if manifest.get('status') != 'validated_partial' or manifest.get('adjustment_type') != 'none':
        raise ValueError('Only a validated, explicitly unadjusted dataset is eligible')
    paths = []
    for entry in manifest['months'].values():
        path = (dataset / entry['file']).resolve()
        if not path.is_relative_to(dataset) or not path.is_file():
            raise ValueError('Invalid or missing dataset partition')
        paths.append(str(path))
    if not paths:
        raise ValueError('Dataset has no published partitions')
    connection = duckdb.connect(config={'memory_limit': '512MB', 'threads': '2'})
    connection.read_parquet(paths, hive_partitioning=False).create_view('daily')
    return connection
