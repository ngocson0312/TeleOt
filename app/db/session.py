from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
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
