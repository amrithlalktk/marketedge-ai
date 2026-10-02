from __future__ import annotations

from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    if url.startswith("sqlite"):
        kw = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url:
            kw["poolclass"] = StaticPool
        return create_engine(url, **kw)
    s = get_settings()
    if s.db_null_pool:  # serverless: no pool survives between invocations; Neon's pooled endpoint does the pooling
        from sqlalchemy.pool import NullPool

        return create_engine(url, poolclass=NullPool, pool_pre_ping=True)
    return create_engine(url, pool_pre_ping=True, pool_size=s.db_pool_size, max_overflow=s.db_max_overflow, pool_recycle=1800)


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
