from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import event, inspect, text
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

log = logging.getLogger(__name__)

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
            # Com WAL, NORMAL é seguro (uma queda de energia pode perder só a última transação, nunca corromper) e
            # torna as escritas das análises muito mais rápidas. Cache de 64 MiB e leituras por mmap (256 MiB).
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA cache_size=-65536")
            cur.execute("PRAGMA mmap_size=268435456")
            cur.execute("PRAGMA temp_store=MEMORY")
            cur.close()

    return _engine


def init_db() -> None:
    from . import models  # noqa: F401  (regista as tabelas)

    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)
    _relax_not_null(engine)
    from .services.audit import seal_on_startup

    seal_on_startup()


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


def _relax_not_null(engine) -> None:
    """Colunas que passaram a aceitar NULL (ex.: detection.reference_id, nulo no modo descoberta).

    O SQLite não permite alterar NOT NULL: a tabela é recriada com o esquema atual e os dados são copiados,
    numa única transação. Só se aplica a tabelas que nenhuma outra referencia por chave estrangeira.
    """
    insp = inspect(engine)
    for table in SQLModel.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        db_cols = {c["name"]: c for c in insp.get_columns(table.name)}
        if not any(col.nullable and not col.primary_key and col.name in db_cols and not db_cols[col.name]["nullable"]
                   for col in table.columns):
            continue
        old = f"_old_{table.name}"
        cols = ", ".join(f'"{c}"' for c in db_cols if c in table.c)
        try:
            with engine.begin() as conn:
                for index in insp.get_indexes(table.name):
                    conn.execute(text(f'DROP INDEX IF EXISTS "{index["name"]}"'))
                conn.execute(text(f'ALTER TABLE "{table.name}" RENAME TO "{old}"'))
                table.create(conn)
                conn.execute(text(f'INSERT INTO "{table.name}" ({cols}) SELECT {cols} FROM "{old}"'))
                conn.execute(text(f'DROP TABLE "{old}"'))
        except Exception:  # a transação é revertida: a tabela antiga fica como estava
            log.exception("Não foi possível migrar a tabela %s (colunas que passaram a aceitar NULL)", table.name)


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
