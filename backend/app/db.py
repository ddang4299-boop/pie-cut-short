"""SQLite engine (SQLModel). One file: data/shortforge.db."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import event
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
    _engine, _engine_path = engine, path
    return engine


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
