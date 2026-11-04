from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine as sqlalchemy_create_engine, event
from sqlalchemy.engine import Engine, URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base


PreWriteHook = Callable[[Engine], None]


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    if dbapi_connection.__class__.__module__.split(".")[0] != "sqlite3":
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def create_storage_engine(
    database_url: str | URL,
    **engine_kwargs: Any,
) -> Engine:
    """Create an engine for the Agent database without touching the source store."""

    engine_kwargs.setdefault("future", True)
    if isinstance(database_url, str) and database_url.startswith("sqlite"):
        connect_args = dict(engine_kwargs.pop("connect_args", {}))
        if ":memory:" in database_url:
            connect_args.setdefault("check_same_thread", False)
            engine_kwargs.setdefault("poolclass", StaticPool)
        engine_kwargs["connect_args"] = connect_args
    return sqlalchemy_create_engine(database_url, **engine_kwargs)


#: Columns added after the first release.  PostgreSQL gets them from the SQL
#: migrations under ``migrations/``; SQLite builds its schema from the ORM
#: metadata, and ``create_all`` never alters an existing table, so local
#: databases need the additive ``ALTER TABLE`` spelled out here.
_SQLITE_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "job_snapshots": {
        "publish_date": "DATE",
        "deadline_date": "DATE",
        "deadline_text": "VARCHAR(64)",
    },
}


def ensure_additive_columns(bind: Engine) -> None:
    """Add later columns to an existing SQLite database, in place.

    Only additive, nullable columns are handled: that is the one shape that can
    be applied safely to a live SQLite file without rebuilding the table.
    Everything else belongs in a real migration.
    """

    if bind.dialect.name != "sqlite":
        return
    with bind.begin() as connection:
        for table, columns in _SQLITE_ADDITIVE_COLUMNS.items():
            existing = {
                str(row[1])
                for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            if not existing:
                continue
            for name, ddl in columns.items():
                if name not in existing:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                    )


def initialize_schema(bind: Engine) -> None:
    """Create the Agent-owned schema; no source SQLite/JSON path is consulted."""

    Base.metadata.create_all(bind=bind)
    ensure_additive_columns(bind)


class Storage:
    """Session and transaction boundary for the Agent-owned database."""

    def __init__(self, engine: Engine, *, pre_write_hook: PreWriteHook | Any = None):
        self.engine = engine
        self.pre_write_hook = pre_write_hook
        self.session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    @classmethod
    def from_url(
        cls,
        database_url: str | URL,
        *,
        pre_write_hook: PreWriteHook | Any = None,
        initialize: bool = False,
        **engine_kwargs: Any,
    ) -> "Storage":
        storage = cls(
            create_storage_engine(database_url, **engine_kwargs),
            pre_write_hook=pre_write_hook,
        )
        if initialize:
            storage.initialize()
        return storage

    def initialize(self) -> None:
        initialize_schema(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self.session_factory() as session:
            yield session

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[Session]:
        """Yield a session whose scope commits or rolls back as one unit.

        ``write=True`` invokes the configured logical-backup hook immediately
        before opening the write transaction. The hook is intentionally
        injected; this layer never shells out to ``pg_dump``.
        """

        if write:
            self._before_write()
        with self.session_factory() as session:
            with session.begin():
                yield session

    def write_transaction(self) -> Any:
        """Return a transaction context that runs the pre-write hook first."""

        return self.transaction(write=True)

    def _before_write(self) -> None:
        hook = self.pre_write_hook
        if hook is None:
            return
        callback = getattr(hook, "before_write", None)
        if callback is not None:
            callback(self.engine)
            return
        hook(self.engine)


__all__ = [
    "PreWriteHook",
    "Storage",
    "create_storage_engine",
    "initialize_schema",
]
