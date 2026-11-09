"""Read-only PostgreSQL audit; no provider requests. Local report excludes public Git."""
import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path
from sqlalchemy import select,func
from app.db.session import SessionLocal
from app.models import ProxyPartition,Event,Daily,Raw,ReconstructionFact,ReconstructionRun,ProxyBatch,Stock
from app.providers.tushare_proxy import PROVIDER
from app.services.calendar import sessions

ROOT=Path(__file__).resolve().parents[1]

def audit(db,start,end,source_id=None):
    expected=sessions(db,start,end)
    stmt=select(ProxyPartition).where(ProxyPartition.endpoint=='limit_list_d',ProxyPartition.trade_date.between(start,end))
    if source_id:stmt=stmt.where(ProxyPartition.source_id==source_id)
    parts=list(db.scalars(stmt))
    if len({p.source_id for p in parts})>1:raise ValueError('specify --source-id; sources cannot be merged')
    parts=[p for p in parts if '_codes' not in p.parameters and set(p.parameters)=={'trade_date'}]
    good={p.trade_date:p for p in parts if p.status=='success' and p.raw_id and not p.error_message}
    missing=sorted(set(expected)-set(good))
    raw_ids=[p.raw_id for p in good.values()]
    events=list(db.scalars(select(Event).where(Event.raw_id.in_(raw_ids)))) if raw_ids else []
    ups=[e for e in events if e.limit_type=='up']
    keys=Counter((e.stock_code,e.trade_date,e.limit_type) for e in events)
    duplicates=sum(v-1 for v in keys.values())
    before_duplicates=sum(p.audit.get('duplicates',0) for p in good.values())
    before_count=sum(p.audit.get('source_rows',0) for p in good.values())
    coverage={k:{'present':sum(getattr(e,k) is not None for e in ups),'denominator':len(ups),
                   'rate':sum(getattr(e,k) is not None for e in ups)/len(ups) if ups else None}
              for k in ('first_limit_time','last_limit_time','consecutive_limit_days','open_times','seal_amount','circulating_market_cap')}
    invalid_dates=sum(e.trade_date not in expected for e in events)
    from app.providers.normalize import stock_code
    invalid_codes=[]
    for e in events:
        try:stock_code(e.stock_code)
        except ValueError:invalid_codes.append(e.stock_code)
    lifecycle={}
    for stock in db.scalars(select(Stock).where(Stock.provider=='baostock').order_by(Stock.id.desc())):
        lifecycle.setdefault(stock.stock_code,stock)
    unknown_lifecycle=sorted({e.stock_code for e in events if e.stock_code not in lifecycle})
    lifecycle_conflicts=sorted({e.stock_code for e in events if e.stock_code in lifecycle and
        ((lifecycle[e.stock_code].listing_date and e.trade_date<lifecycle[e.stock_code].listing_date) or
         (lifecycle[e.stock_code].delisting_date and e.trade_date>lifecycle[e.stock_code].delisting_date))})
    # Common classified sample only; do not claim universe precision without overlap.
    facts=list(db.scalars(select(ReconstructionFact).join(ReconstructionRun).where(ReconstructionFact.trade_date.between(start,end),
        ReconstructionRun.status=='ready').order_by(ReconstructionRun.created_at.desc())))
    by_key={}
    for f in facts:by_key.setdefault((f.stock_code,f.trade_date),f)
    known={k:f for k,f in by_key.items() if f.trade_date in good and f.classification in ('up','not_up','suspended','no_limit')}
    observed={(e.stock_code,e.trade_date):e for e in ups}
    tp=[k for k,f in known.items() if f.classification=='up' and k in observed]
    fp=[k for k,f in known.items() if f.classification=='up' and k not in observed]
    fn=[k for k,f in known.items() if f.classification!='up' and k in observed]
    comparison={'common_classified_stock_days':len(known),'tp':len(tp) if known else None,'fp':len(fp) if known else None,'fn':len(fn) if known else None,
                'reason':None if known else 'No existing BaoStock reconstruction overlaps this month; no artificial comparison.',
                'false_positives':[(c,str(d)) for c,d in fp],'false_negatives':[(c,str(d)) for c,d in fn]}
    result={'start':str(start),'end':str(end),'source_id':parts[0].source_id if parts else None,
            'calendar_sessions':len(expected),'expected_dates':[str(d) for d in expected],'success_dates':[str(d) for d in sorted(good)],
            'missing_dates':[str(d) for d in missing],'counts':dict(Counter(e.limit_type for e in events)),
            'field_coverage_up_only':coverage,'source_duplicate_rows':before_duplicates,
            'source_duplicate_rate':before_duplicates/before_count if before_count else None,
            'stored_duplicate_rows':duplicates,'stored_duplicate_rate':duplicates/len(events) if events else None,
            'invalid_dates':invalid_dates,'invalid_codes':invalid_codes,'unknown_lifecycle_codes':unknown_lifecycle,
            'lifecycle_conflicts':lifecycle_conflicts,'comparison':comparison,
            'per_day':[{'date':str(d),'raw_id':good[d].raw_id,**good[d].audit} for d in sorted(good)],
            'preserved_daily_counts':dict(db.execute(select(Daily.provider,func.count()).group_by(Daily.provider)).all()),
            'preserved_event_counts':dict(db.execute(select(Event.provider,func.count()).group_by(Event.provider)).all()),
            'passed':bool(expected) and not missing and not invalid_dates and not invalid_codes and not duplicates and not lifecycle_conflicts,
            'limitations':['Dates verified against stored SSE trading calendar; security identifiers syntactically validated, not proven complete historical universe.',
                           'Counts reflect proxy non-ST endpoint scope, not a claim of all A shares.','Non-null field coverage is not independent accuracy validation.']}
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--start',type=date.fromisoformat,default=date(2020,12,1));p.add_argument('--end',type=date.fromisoformat,default=date(2020,12,31));p.add_argument('--source-id')
    a=p.parse_args()
    with SessionLocal() as db:r=audit(db,a.start,a.end,a.source_id)
    out=ROOT/'data/v03-month-audit.json';out.write_text(json.dumps(r,ensure_ascii=False,indent=2,default=str))
    lines=['# Tushare 首月实际验收','',f"范围：{r['start']} — {r['end']}；交易日 {r['calendar_sessions']}；日期覆盖校验通过：{r['passed']}。",'',
           '```json',json.dumps(r,ensure_ascii=False,indent=2,default=str),'```','',
           '本报告只读数据库；原始响应在 data/tushare_proxy，未发布 GitHub。BaoStock 无重叠时不伪造准确率。']
    (ROOT/'docs/tushare_month_validation.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:r[k] for k in ('calendar_sessions','missing_dates','counts','field_coverage_up_only','stored_duplicate_rate','comparison','passed')},ensure_ascii=False))
if __name__=='__main__':main()
