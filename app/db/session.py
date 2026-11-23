from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base


class Database:
    def __init__(self, url: str):
        connect_args = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False  # sync jobs write from a worker thread
            path = url.split("///", 1)[1] if "///" in url else ""
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(url, connect_args=connect_args)
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", _sqlite_pragmas)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """create_all() makes new tables but never new columns; add nullable ones here."""
        insp = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                existing = {c["name"] for c in insp.get_columns(table.name)}
                for col in table.columns:
                    if col.name in existing:
                        continue
                    if not col.nullable:
                        raise RuntimeError(f"Cột mới {table.name}.{col.name} phải cho phép NULL")
                    col_type = col.type.compile(self.engine.dialect)
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}'))

    @contextmanager
    def session(self) -> Iterator[Session]:
        """One transaction: commit on success, roll back on error."""
        s = self._sessions()
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise
        finally:
            s.close()


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=10000")
    cur.close()
