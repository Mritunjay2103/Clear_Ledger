"""Engine and session management.

SQLite needs deliberate configuration to behave under concurrent writes: WAL for
reader/writer separation, a busy timeout so a contending writer waits instead of
failing instantly, and ``BEGIN IMMEDIATE`` for the transaction that reserves
funds so two workers cannot both act on a stale PO balance.

pysqlite's implicit transaction handling is disabled (``isolation_level = None``)
and SQLAlchemy emits the BEGIN itself, which is the only way to choose the
locking mode per transaction.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.models import Base

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None

_immediate_begin: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "sqlite_immediate_begin", default=False
)


def _configure_sqlite(dbapi_connection, _record) -> None:
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.close()


def _emit_begin(conn) -> None:
    conn.exec_driver_sql("BEGIN IMMEDIATE" if _immediate_begin.get() else "BEGIN")


def build_engine(database_url: str | None = None) -> Engine:
    url = database_url or settings.database_url
    engine = create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    event.listen(engine, "connect", _configure_sqlite)
    event.listen(engine, "begin", _emit_begin)
    return engine


def get_engine() -> Engine:
    global _engine, _SessionFactory
    if _engine is None:
        settings.ensure_directories()
        _engine = build_engine()
        _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionFactory is not None
    return _SessionFactory


def create_all(engine: Engine | None = None) -> None:
    Base.metadata.create_all(engine or get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    """A transactional session. Each worker task creates its own."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def immediate_transaction(session: Session) -> Iterator[Session]:
    """Run a block inside a SQLite write transaction that locks immediately.

    The commit-decision step re-reads PO commitments, re-checks duplicates and
    inserts the reservation; taking the write lock at BEGIN keeps that sequence
    serialisable rather than optimistic.
    """
    session.rollback()
    token = _immediate_begin.set(True)
    try:
        session.begin()
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        _immediate_begin.reset(token)


def get_db() -> Iterator[Session]:
    """FastAPI request-scoped session dependency."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def reset_engine_for_tests(database_url: str) -> Engine:
    """Rebind the module-level engine. Used only by the test suite."""
    global _engine, _SessionFactory
    _engine = build_engine(database_url)
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine
