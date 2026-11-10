"""Inventory historical ST gaps and optionally backfill a bounded sample via BaoStock."""
import argparse,json
from pathlib import Path
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor
from app.research.unified import UnifiedResearch
from app.providers.client import Client
from app.collectors.history import ingest_symbol
from app.db.session import SessionLocal


def audit(limit=0):
 root=Path('data/historical_st_audit')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');root.mkdir(parents=True)
 with UnifiedResearch() as u:
  def rows(sql):
   c=u.db.execute(sql);names=[x[0] for x in c.description];return [dict(zip(names,r)) for r in c.fetchall()]
  yearly=rows("""SELECT year(trade_date) AS year,count(*) AS rows,count(*) FILTER(WHERE is_st IS TRUE) AS st,
   count(*) FILTER(WHERE is_st IS FALSE) AS non_st,count(*) FILTER(WHERE is_st IS NULL) AS unknown,
   count(*) FILTER(WHERE status_conflict) AS conflicts FROM stock_daily WHERE price_quality='verified' GROUP BY 1 ORDER BY 1""")
  gaps=rows("""SELECT d.stock_code,any_value(s.stock_name) AS stock_name,min(d.trade_date) AS start,max(d.trade_date) AS end,
   count(*) FILTER(WHERE d.is_st IS NULL) AS unknown_days FROM stock_daily d LEFT JOIN stocks s USING(stock_code)
   WHERE d.price_quality='verified' GROUP BY d.stock_code HAVING count(*) FILTER(WHERE d.is_st IS NULL)>0 ORDER BY unknown_days DESC,d.stock_code""")
  report=dict(checked_at=datetime.now(timezone.utc).isoformat(),snapshot=str(u.path),yearly=yearly,gaps=gaps,
    policy='Current names only prioritize collection; each historical date uses provider isST. Unknown is never non-ST.',collections=[])
  selected=[x for x in gaps if 'ST' in (x['stock_name'] or '').upper()][:limit]
 def collect(item):
  result=dict(stock_code=item['stock_code'],stock_name=item['stock_name'],start=item['start'],end=item['end'])
  try:
   with SessionLocal() as db:
    received,inserted,raw=ingest_symbol(db,item['stock_code'],item['start'],item['end'],Client('baostock',retries=0,timeout=40))
    result.update(status='collected',received=received,inserted=inserted,raw_id=raw)
  except Exception as e:result.update(status='failed',error=str(e)[:350])
  print(result['stock_code'],result['status'],result.get('received'),flush=True);return result
 if limit:
  with ThreadPoolExecutor(max_workers=2) as pool:report['collections']=list(pool.map(collect,selected))
 (root/'report.json').write_text(json.dumps(report,default=str,ensure_ascii=False,indent=2))
 p=root.parent/'latest.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps({'dataset':root.name}));tmp.replace(p)
 print('Report:',root,flush=True)
 return report
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--collect-sample',type=int,default=0,choices=range(0,11));args=p.parse_args();audit(args.collect_sample)
