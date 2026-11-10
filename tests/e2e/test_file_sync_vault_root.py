"""A one-file sync renders the file as a whole-vault sync does, and leaves no stale render.

The vault is found from the file (the nearest folder holding `.obsidian`), so the source
field, the wikilinks and the images name the vault, not the file's folder. A render cached
under another vault root (here, the file's folder) is not reused by the next sync: the
cached render's key covers the root, so the card is rendered again and Anki corrected.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from arete.application.config import AppConfig, resolve_config
from arete.composition.orchestrator import SyncFailedError, run_sync_logic
from tests.e2e.sync_harness import BACKENDS, Backend, DirectBackend, make_backend, run_sync

ID = "arete_01J00000000000000000000301"
CARD = f"""---
arete: true
deck: Stats
model: Basic
cards:
- id: {ID}
  Front: State Bayes' rule.
  Back: See [[Prior]]. ![[pic.png]]
---

Body.
"""
# A 1x1 PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _world(root: Path, name: str) -> tuple[Path, Path, Backend, dict[str, Any]]:
    vault = root / "My Vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / "attachments").mkdir()
    (vault / "attachments" / "pic.png").write_bytes(PNG)
    (vault / "courses").mkdir()
    md = vault / "courses" / "Bayes.md"
    md.write_text(CARD, encoding="utf-8")
    backend = make_backend(name, root)
    (root / "media").mkdir()
    common = {
        "anki_base": backend.anki_base if isinstance(backend, DirectBackend) else root,
        "anki_media_dir": root / "media",
        "cache_db": str(root / "cache.db"),
        "log_dir": root / "logs",
        "workers": 1,
    }
    return vault.resolve(), md.resolve(), backend, common


def _held(backend: Backend) -> tuple[dict[str, str], ...]:
    return tuple(dict(n.fields) for n in backend.notes())


async def _full_sync(backend: Backend, vault: Path, common: dict[str, Any]) -> None:
    await run_sync(backend, resolve_config({"root_input": vault, **common}))


@pytest.mark.asyncio
@pytest.mark.parametrize("name", BACKENDS)
@pytest.mark.parametrize("first", ["file-sync", "render-under-the-folder"])
async def test_a_file_sync_then_a_full_sync_equal_a_full_sync_alone(
    tmp_path: Path, name: str, first: str
):
    vault, _md, alone, common = _world(tmp_path / "alone", name)
    try:
        await _full_sync(alone, vault, common)
        expected = _held(alone)
    finally:
        alone.close()
    [fields] = expected
    assert fields["_obsidian_source"].startswith("My Vault|courses/Bayes.md|")
    assert "vault=My%20Vault" in fields["Back"] and "<img" in fields["Back"]

    vault, md, backend, common = _world(tmp_path / "file", name)
    try:
        if first == "file-sync":
            config = resolve_config(
                {"root_input": md, "force": True, "clear_cache": True, **common}
            )
            assert config.vault_root == vault
        else:
            # A render cached under the file's folder as the vault root.
            config = AppConfig(
                root_input=md, vault_root=md.parent, force=True, clear_cache=True, **common
            )
        await run_sync(backend, config)
        await _full_sync(backend, vault, common)

        assert _held(backend) == expected
        assert sorted(p.name for p in (tmp_path / "file" / "media").iterdir()) == sorted(
            p.name for p in (tmp_path / "alone" / "media").iterdir()
        )
    finally:
        backend.close()


@pytest.mark.asyncio
async def test_a_file_in_no_vault_is_refused_before_anything_is_read(tmp_path: Path):
    folder = tmp_path / "loose"
    folder.mkdir()
    md = folder / "Bayes.md"
    md.write_text(CARD, encoding="utf-8")
    config = resolve_config(
        {
            "root_input": md,
            "anki_base": tmp_path / "anki",
            "anki_media_dir": tmp_path / "media",
            "cache_db": str(tmp_path / "cache.db"),
            "log_dir": tmp_path / "logs",
        }
    )

    with pytest.raises(SyncFailedError, match="Cannot tell which vault"):
        await run_sync_logic(config)

    assert md.read_text(encoding="utf-8") == CARD
    assert not (tmp_path / "cache.db").exists() and not (tmp_path / "logs").exists()
