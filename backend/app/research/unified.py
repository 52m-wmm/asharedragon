"""Read-only access to an atomically published, immutable research release."""
import json
from pathlib import Path

import duckdb
from app.research.queries import DEFAULT_ROOT


class UnifiedResearch:
    def __init__(self, root=DEFAULT_ROOT):
        base = Path(root).resolve() / 'research/unified'
        manifest = json.loads((base / 'manifest.json').read_text())
        self.path = (base / manifest['dataset']).resolve()
        if not self.path.is_relative_to(base):
            raise ValueError('Research snapshot escaped root')
        self.summary = json.loads((self.path / 'summary.json').read_text())
        self.db = duckdb.connect(str(self.path / 'research.duckdb'), read_only=True)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def daily(self, *, year, month=None, code=None, page=1, page_size=200):
        if not 2017 <= year <= 2026 or (month is not None and not 1 <= month <= 12):
            raise ValueError('Invalid research year/month')
        if page < 1 or not 1 <= page_size <= 1000:
            raise ValueError('Invalid pagination')
        conditions = ['year=?']
        params = [year]
        if month is not None:
            conditions.append('month=?')
            params.append(month)
        if code is not None:
            conditions.append('stock_code=?')
            params.append(code)
        where = ' AND '.join(conditions)
        result = self.db.execute('SELECT * FROM stock_daily WHERE '+where+
            ' ORDER BY trade_date,stock_code LIMIT ? OFFSET ?', params+[page_size,(page-1)*page_size])
        columns = [c[0] for c in result.description]
        return {'data': [dict(zip(columns,row)) for row in result.fetchall()],
            'meta': {'version': self.summary['version'], 'snapshot': self.path.name,
                'data_end': self.summary['data_end'], 'page': page, 'page_size': page_size,
                'quality_scope': 'Use purpose-specific quality columns; verified price is not a verified label',
                'formal_whole_market_labels': self.summary['formal_whole_market_labels']}}
