"""One sync at a time per cache: an exclusive, non-blocking lock on a file beside the cache.

Two syncs at once each find no note for a new card and each create one, so every new card
is made twice. The lock is taken before the sync reads anything and held to its end.

It is an OS lock on an open file (`flock` on POSIX, `msvcrt.locking` on Windows), so the
OS releases it when the process ends, however it ends; a lock file left behind holds
nothing. `flock` locks belong to the open file, so two syncs inside one process also
exclude each other.
"""

from __future__ import annotations

import errno
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class LockHeld(Exception):
    """Another process (or another sync in this one) holds the lock."""

    def __init__(self, path: Path):
        """Name the lock file that is held."""
        super().__init__(f"lock is held: {path}")
        self.path = path


def lock_path_for(cache_path: Path) -> Path:
    """The lock file of a cache database: beside it, named after it."""
    return cache_path.with_name(cache_path.name + ".lock")


if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EDEADLK):
                return False
            raise
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Hold the lock on `path` for the block; raise `LockHeld` at once if it is taken.

    Any other failure to lock (a file system without locks) raises as it is: it says
    nothing about another sync.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if not _try_lock(fd):
            raise LockHeld(path)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)
