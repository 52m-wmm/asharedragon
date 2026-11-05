"""One local durable queue worker; Postgres advisory lock prevents duplicate runners."""
import logging
import time
from sqlalchemy import select, text
from app.db.session import engine, SessionLocal
from app.models import Job, now
from app.collectors.ingest import run_job

def main():
    logging.basicConfig(level=logging.INFO,format='%(message)s')
    with engine.connect() as lock:
        if not lock.scalar(text('SELECT pg_try_advisory_lock(68142719)')):
            raise SystemExit('A worker is already running')
        with SessionLocal() as db:
            for job in db.scalars(select(Job).where(Job.status=='running')):
                job.status='interrupted';job.error_message='Worker stopped; retry resumes successful datasets';job.finished_at=now()
            db.commit()
        while True:
            with SessionLocal() as db:
                job=db.scalar(select(Job).where(Job.status=='queued').order_by(Job.started_at).limit(1))
                if job: run_job(db,job.job_id)
            time.sleep(1)
if __name__=='__main__': main()
