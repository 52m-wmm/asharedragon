"""All integration fixtures live in an isolated PostgreSQL schema and are rolled back."""
import os
import sys
from pathlib import Path
import subprocess
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from app.core.config import settings

@pytest.fixture(scope='session')
def test_engine():
    schema='test_'+uuid4().hex
    admin=create_engine(settings.database_url)
    with admin.begin() as connection:connection.execute(text(f'CREATE SCHEMA {schema}'))
    url=make_url(settings.database_url).update_query_dict({'options':f'-csearch_path={schema}'})
    env={**os.environ,'DATABASE_URL':url.render_as_string(hide_password=False)}
    try:
        subprocess.run([sys.executable,'-m','alembic','-c',str(Path(__file__).resolve().parents[1]/'alembic.ini'),'upgrade','head'],env=env,check=True,capture_output=True)
        engine=create_engine(url)
        yield engine
        engine.dispose()
        subprocess.run([sys.executable,'-m','alembic','-c',str(Path(__file__).resolve().parents[1]/'alembic.ini'),'downgrade','base'],env=env,check=True,capture_output=True)
        subprocess.run([sys.executable,'-m','alembic','-c',str(Path(__file__).resolve().parents[1]/'alembic.ini'),'upgrade','head'],env=env,check=True,capture_output=True)
    finally:
        with admin.begin() as connection:connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin.dispose()

@pytest.fixture
def db(test_engine):
    with test_engine.connect() as connection:
        transaction=connection.begin()
        with Session(bind=connection,join_transaction_mode='create_savepoint',expire_on_commit=False) as session:
            yield session
        transaction.rollback()
