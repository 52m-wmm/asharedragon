"""Inspect gaps by default; --apply retries missing partitions with bounded requests."""
import argparse
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
from app.db.session import SessionLocal
from app.collectors.repair import plan_pool_repairs,repair_pools
from app.providers.client import Client


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start',type=date.fromisoformat,required=True)
    parser.add_argument('--end',type=date.fromisoformat,required=True)
    parser.add_argument('--kind',action='append',choices=['up','broken','down'])
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--refresh-statistics',action='store_true',help='补采后顺序重建历史统计与分类快照')
    args=parser.parse_args()
    if args.refresh_statistics and not args.apply:
        parser.error('--refresh-statistics 需要 --apply')
    if (args.end-args.start).days>31:
        parser.error('每批最多32个日历日，请分批补采')
    with SessionLocal() as db:
        plan=plan_pool_repairs(db,args.start,args.end,args.kind)
        print(json.dumps({'missing':plan},default=str,ensure_ascii=False),flush=True)
        if args.apply:
            results=repair_pools(db,args.start,args.end,args.kind,Client(retries=1,timeout=20))
            print(json.dumps({'results':results,'remaining':plan_pool_repairs(db,args.start,args.end,args.kind)},default=str,ensure_ascii=False))
            if args.refresh_statistics:
                root=Path(__file__).resolve().parents[1]
                env={**os.environ,'PYTHONPATH':str(root/'backend')}
                for script in ('finalize_research.py','build_history_limits.py','classify_dragon_streaks.py'):
                    subprocess.run([sys.executable,str(root/'scripts'/script)],cwd=root,env=env,check=True)
            else:
                print('历史统计尚未刷新；加 --refresh-statistics 可在补采后重建快照。')
            return int(any(r['status']!='success' for r in results))
    return 0

if __name__=='__main__':raise SystemExit(main())
