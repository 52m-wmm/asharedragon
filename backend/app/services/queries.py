from sqlalchemy import select, func
from sqlalchemy.orm import aliased
from app.models import Event, Daily, Raw

def latest_events(db, provider, start=None, end=None, as_of=None):
    """Select a complete latest successful pool snapshot, not a union of revisions."""
    q=select(Raw.id,func.row_number().over(partition_by=(Raw.provider,Raw.endpoint,Raw.trade_date),order_by=Raw.id.desc()).label('rn')).where(Raw.provider==provider,Raw.ingestion_status=='success')
    if as_of is not None: q=q.where(Raw.fetched_at<=as_of)
    r=q.subquery()
    stmt=select(Event).join(r,Event.raw_id==r.c.id).where(r.c.rn==1)
    if start: stmt=stmt.where(Event.trade_date>=start)
    if end: stmt=stmt.where(Event.trade_date<=end)
    return list(db.scalars(stmt))

def latest_daily(db, provider, code, start=None, end=None, as_of=None):
    q=select(Daily,func.row_number().over(partition_by=(Daily.stock_code,Daily.trade_date,Daily.adjustment),order_by=Daily.id.desc()).label('rn')).where(Daily.provider==provider,Daily.stock_code==code)
    if as_of: q=q.where(Daily.collected_at<=as_of)
    sub=q.subquery(); obj=aliased(Daily,sub)
    stmt=select(obj).where(sub.c.rn==1)
    if start: stmt=stmt.where(obj.trade_date>=start)
    if end: stmt=stmt.where(obj.trade_date<=end)
    return list(db.scalars(stmt.order_by(obj.trade_date)))

def serialize(obj):
    return {col.name:getattr(obj,col.name) for col in obj.__table__.columns}
