"""Two syncs at once against one cache: the second does nothing, so no card is created twice.

Each `execute_sync` gets its own adapter, NoteSync and cache connection, as two `arete sync`
processes would; the lock is what stops them both finding no note and both creating one.
The two-process case is `tests/interface/test_cli_sync_lock.py`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from arete.composition.orchestrator import SyncLocked, execute_sync
from tests.e2e.sync_harness import BACKENDS, make_backend, sync_config, vault_cards

TWO_CARDS = (
    "---\narete: true\ndeck: Stats\nmodel: Basic\ncards:\n"
    "- Front: State Bayes' rule.\n  Back: P(A|B) = P(B|A)P(A)/P(B)\n"
    "- Front: What is a conjugate prior?\n  Back: Same family posterior.\n"
    "---\n\nBody.\n"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", BACKENDS)
async def test_a_second_sync_while_one_runs_is_refused_and_creates_nothing(
    name: str, tmp_path: Path
):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Bayes.md").write_text(TWO_CARDS, encoding="utf-8")
    backend = make_backend(name, tmp_path)
    config = sync_config(tmp_path, vault, backend)

    async def _bridge(_config):
        return backend.bridge()

    try:
        with patch("arete.composition.orchestrator.get_anki_bridge", _bridge):
            first, second = await asyncio.gather(
                execute_sync(config), execute_sync(config), return_exceptions=True
            )
        notes = backend.notes()
        assert len(notes) == 2, "one note per card"
        ids = vault_cards(vault)
        assert {v["nid"] for v in ids.values()} == {str(n.nid) for n in notes}
        assert not isinstance(first, BaseException), first
        assert first.total_errors == 0
        assert isinstance(second, SyncLocked)
    finally:
        backend.close()
