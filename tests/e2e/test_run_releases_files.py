"""A finished sync holds no file open: Windows cannot delete or replace an open file.

The run log, the cache database and the sync lock are each opened for one run and closed
by it, so the folder holding them can be removed as soon as the sync returns. On POSIX the
removal would succeed anyway; the handler check is what fails there.
"""

from __future__ import annotations

import logging
import shutil

import pytest

from tests.e2e.sync_harness import BACKENDS, make_backend, run_sync, sync_config

NOTE = "---\narete: true\ndeck: Files::Held\ncards:\n- Front: q\n  Back: a\n---\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_name", BACKENDS)
async def test_after_a_sync_its_log_cache_and_lock_can_be_deleted(backend_name, tmp_path):
    root = tmp_path / "run"
    vault = root / "vault"
    vault.mkdir(parents=True)
    (vault / "n.md").write_text(NOTE, encoding="utf-8")
    backend = make_backend(backend_name, root)
    try:
        stats, _ = await run_sync(backend, sync_config(root, vault, backend))
    finally:
        backend.close()

    assert stats.total_errors == 0
    assert list((root / "logs").glob("run_*.log")), "the run wrote its log"
    assert logging.getLogger("arete").handlers == []
    shutil.rmtree(root)
