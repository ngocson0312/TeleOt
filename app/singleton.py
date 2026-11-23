"""One bot per machine. Two bots polling the same token fight over updates (Telegram 409 Conflict)
and both run the schedule, so the second copy refuses to start."""
from __future__ import annotations

import os
from pathlib import Path
from typing import IO


def acquire_lock(path: Path) -> IO | None:
    """Lock `path` for the life of this process. None if another process holds it.
    Keep the returned file open: the OS releases the lock when the process ends, even on a crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f
