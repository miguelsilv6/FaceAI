from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import event, inspect, text
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        settings.ensure_dirs()
        # timeout: espera até 30 s por um lock de escrita (análise + uploads em simultâneo) em vez de falhar logo
        _engine = create_engine(settings.db_url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return _engine


def init_db() -> None:
    from . import models  # noqa: F401  (regista as tabelas)

    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)


def _add_missing_columns(engine) -> None:
    """Migração aditiva: acrescenta colunas novas a tabelas já existentes (bases de dados de versões anteriores).

    Só adiciona colunas (com valor por omissão); nunca remove nem altera dados existentes.
    """
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                coltype = col.type.compile(dialect=engine.dialect)
                default = _sql_default(col)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}{default}'))
            # create_all não cria índices em tabelas que já existiam (ex.: detection.track_id)
            for index in table.indexes:
                index.create(conn, checkfirst=True)


def _sql_default(col) -> str:
    default = getattr(col.default, "arg", None)
    if isinstance(default, bool):
        return f" NOT NULL DEFAULT {int(default)}"
    if isinstance(default, (int, float)):
        return f" NOT NULL DEFAULT {default}"
    if isinstance(default, str):
        return " NOT NULL DEFAULT '" + default.replace("'", "''") + "'"
    return ""


def get_session() -> Iterator[Session]:
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session
