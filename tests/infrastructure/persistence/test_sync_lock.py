"""The sync lock: held by one process at a time, refused at once, freed when its holder dies."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from arete.infrastructure.persistence.sync_lock import LockHeld, exclusive_lock, lock_path_for

HOLDER = """
import pathlib, sys
from arete.infrastructure.persistence.sync_lock import exclusive_lock
with exclusive_lock(pathlib.Path(sys.argv[1])):
    print("held", flush=True)
    sys.stdin.read()
"""


@contextmanager
def held_by_another_process(path: Path) -> Iterator[subprocess.Popen[str]]:
    """A second Python process holding the lock until it is killed or its stdin closes."""
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "held"
        yield proc
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_the_lock_file_sits_beside_the_cache(tmp_path: Path):
    assert lock_path_for(tmp_path / "cache.db") == tmp_path / "cache.db.lock"


def test_a_lock_another_process_holds_is_refused_at_once(tmp_path: Path):
    path = tmp_path / "sub" / "cache.db.lock"
    with held_by_another_process(path):
        with pytest.raises(LockHeld) as refused:
            with exclusive_lock(path):
                pytest.fail("the lock was taken while another process held it")
    assert refused.value.path == path


def test_the_lock_is_free_once_its_holder_is_killed(tmp_path: Path):
    path = tmp_path / "cache.db.lock"
    with held_by_another_process(path) as holder:
        holder.kill()
        holder.wait(timeout=10)
        with exclusive_lock(path):
            pass
    assert path.exists(), "the file stays; only the OS lock on it goes"


def test_two_syncs_in_one_process_exclude_each_other(tmp_path: Path):
    path = tmp_path / "cache.db.lock"
    with exclusive_lock(path):
        with pytest.raises(LockHeld):
            with exclusive_lock(path):
                pass
    with exclusive_lock(path):
        pass
