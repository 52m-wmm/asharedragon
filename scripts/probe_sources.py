"""Real network probes, isolated subprocesses with hard deadlines. No generated data."""
import argparse
import importlib
import inspect
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENDPOINTS = ['stock_zt_pool_em', 'stock_zt_pool_zbgc_em', 'stock_zt_pool_dtgc_em',
             'stock_zt_pool_previous_em', 'stock_zh_a_hist', 'stock_zh_a_spot_em',
             'stock_board_concept_name_em', 'stock_board_concept_cons_em', 'stock_lhb_detail_em',
             'tool_trade_date_hist_sina']

def child(endpoint, params):
    ak = importlib.import_module('akshare')
    fn = getattr(ak, endpoint)
    print(json.dumps({'version': ak.__version__, 'signature': str(inspect.signature(fn))}), flush=True)
    start = time.monotonic()
    try:
        df = fn(**params)
        print(json.dumps({'status': 'success' if len(df) else 'empty_unverified', 'rows': len(df),
                          'columns': list(df.columns), 'seconds': round(time.monotonic()-start, 3),
                          'records': json.loads(df.to_json(orient='records', date_format='iso', force_ascii=False))}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc), 'seconds': round(time.monotonic()-start, 3)}))

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--child'); p.add_argument('--params', default='{}')
    p.add_argument('--recent', default='20261104'); p.add_argument('--timeout', type=int, default=40)
    args = p.parse_args()
    if args.child:
        child(args.child, json.loads(args.params)); sys.exit()
    import akshare as ak
    out = ROOT / 'data/probes'; out.mkdir(parents=True, exist_ok=True)
    signatures = {e: str(inspect.signature(getattr(ak, e))) for e in ENDPOINTS}
    (out/'signatures.json').write_text(json.dumps({'version': ak.__version__, 'signatures': signatures}, indent=2))
    cases = [('stock_zt_pool_em', {'date': d}) for d in [args.recent, '20240115', '20220615']]
    cases += [('stock_zh_a_hist', {'symbol':'000001','period':'daily','start_date':'20240102','end_date':'20240131','adjust':''}),
              ('tool_trade_date_hist_sina', {})]
    results = []
    for endpoint, params in cases:
        started = datetime.now(timezone.utc).isoformat(); t = time.monotonic()
        try:
            r = subprocess.run([sys.executable,__file__,'--child',endpoint,'--params',json.dumps(params)], capture_output=True,text=True,timeout=args.timeout)
            lines = [json.loads(line) for line in r.stdout.splitlines() if line.startswith('{')]
            result = lines[-1] if lines else {'status':'failed','error':r.stderr[-2000:]}
        except subprocess.TimeoutExpired:
            result = {'status':'timeout','error':f'hard timeout {args.timeout}s'}
        item = {'provider':'akshare','endpoint':endpoint,'params':params,'fetched_at':started,'elapsed':round(time.monotonic()-t,2),**result}
        path = out/f'{endpoint}_{params.get("date",params.get("start_date","calendar"))}.json'
        path.write_text(json.dumps(item,ensure_ascii=False,indent=2))
        summary = {k:v for k,v in item.items() if k!='records'}; summary['raw_path']=str(path.relative_to(ROOT))
        results.append(summary); print(json.dumps(summary,ensure_ascii=False),flush=True)
    (out/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
