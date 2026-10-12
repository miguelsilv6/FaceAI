from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        settings.ensure_dirs()
        _engine = create_engine(settings.db_url, connect_args={"check_same_thread": False})

        @event.listens_for(_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return _engine


def init_db() -> None:
    from . import models  # noqa: F401  (regista as tabelas)

    SQLModel.metadata.create_all(get_engine())


def get_session() -> Iterator[Session]:
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session
