"""SQLite engine (SQLModel). One file: data/shortforge.db."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

_engine: Engine | None = None
_engine_path: Path | None = None


def _sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")  # readers don't block the job workers
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def init_db(path: Path) -> Engine:
    """Create (or reuse) the engine for `path` and make sure all tables exist."""
    global _engine, _engine_path
    from app import models  # noqa: F401  (registers the tables)

    path = Path(path)
    if _engine is not None and _engine_path == path:
        return _engine
    if _engine is not None:
        _engine.dispose()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)
    _engine, _engine_path = engine, path
    return engine


def _add_missing_columns(engine: Engine) -> None:
    """Tiny forward-only migration: add columns that exist in the models but not in the DB file.

    create_all() never alters existing tables, so new optional fields would otherwise break old DBs.
    New columns are added as nullable with the model's scalar default (if any) as SQL DEFAULT.
    """
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                default = column.default.arg if column.default is not None and column.default.is_scalar else None
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'
                if isinstance(default, bool):
                    ddl += f" DEFAULT {int(default)}"
                elif isinstance(default, (int, float)):
                    ddl += f" DEFAULT {default}"
                elif isinstance(default, str):
                    ddl += " DEFAULT '" + default.replace("'", "''") + "'"
                conn.execute(text(ddl))


def engine() -> Engine:
    if _engine is None:
        raise RuntimeError("init_db() has not been called")
    return _engine


@contextmanager
def session() -> Iterator[Session]:
    with Session(engine(), expire_on_commit=False) as s:
        yield s


def dispose() -> None:
    global _engine, _engine_path
    if _engine is not None:
        _engine.dispose()
    _engine, _engine_path = None, None
