"""Plan missing stock-pool partitions and repair through the existing ingestion path."""
from sqlalchemy import select
from app.models import Coverage
from app.services.calendar import sessions
from app.providers.client import AK_ENDPOINTS, Client
from app.collectors.ingest import new_job, run_job


def pool_kinds(kinds=None):
    result=list(dict.fromkeys(AK_ENDPOINTS if kinds is None else kinds))
    if not result or any(k not in AK_ENDPOINTS for k in result):
        raise ValueError('股票池类型必须是 up、broken 或 down')
    return result


def plan_pool_repairs(db,start,end,kinds=None):
    kinds=pool_kinds(kinds)
    dates=sessions(db,start,end)
    covered={(r.trade_date,r.dataset) for r in db.scalars(select(Coverage).where(
        Coverage.provider=='akshare',Coverage.status=='success',Coverage.trade_date.between(start,end)))}
    return [dict(trade_date=d,kinds=missing) for d in dates
            if (missing:=[k for k in kinds if (d,k) not in covered])]


def repair_pools(db,start,end,kinds=None,client=None):
    client=client or Client()
    if client.name!='akshare':
        raise ValueError('当前股票池标准化补采仅支持 AKShare')
    results=[]
    for partition in plan_pool_repairs(db,start,end,kinds):
        day=partition['trade_date']
        job=new_job(db,'pools',day,day,kinds=partition['kinds'])
        job=run_job(db,job.job_id,client)
        results.append(dict(trade_date=day,kinds=partition['kinds'],job_id=job.job_id,
                            status=job.status,rows_inserted=job.rows_inserted))
    return results
