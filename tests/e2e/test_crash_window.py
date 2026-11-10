"""A sync killed between the Anki write and the vault write-back, on a real collection."""

from pathlib import Path

import pytest

from arete.application.sync.vault_service import VaultService
from arete.composition.orchestrator import execute_sync
from tests.e2e.conftest import open_collection


@pytest.mark.asyncio
async def test_prune_after_a_crash_before_write_back_keeps_the_note(
    single_card_vault: Path, anki_base: Path, make_config, monkeypatch
):
    """The first run creates the note and caches it, then dies before writing the nid.

    The second run finds the card unchanged and sends nothing. A confirmed prune in that
    run must still see the note as the vault's, not as an orphan.
    """

    def crash(*_args, **_kwargs):
        raise RuntimeError("killed before write-back")

    with monkeypatch.context() as m:
        m.setattr(VaultService, "apply_updates", crash)
        with pytest.raises(RuntimeError, match="killed before write-back"):
            await execute_sync(make_config(single_card_vault))
    md = single_card_vault / "Transformer.md"
    assert "nid:" not in md.read_text(encoding="utf-8")

    col = open_collection(anki_base)
    try:
        created = col.find_notes("")
    finally:
        col.close()
    assert len(created) == 1

    stats = await execute_sync(make_config(single_card_vault, prune=True), confirm_prune=True)

    assert stats.prune is not None and stats.prune.notes == []
    col = open_collection(anki_base)
    try:
        assert col.find_notes("") == created, "the crash-created note survives prune"
    finally:
        col.close()
