"""VaultService when the cache or the disk misbehaves, and when a write-back target moved."""

import logging
import os
from unittest.mock import MagicMock

import pytest

from arete.application.sync.vault_service import VaultService
from arete.domain.models import UpdateItem
from arete.infrastructure.persistence.cache import ContentCache
from tests.factories import anki_note, card, write_note


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture
def cache(tmp_path):
    return ContentCache(db_path=tmp_path / "cache.db")


def _update(path, *, index=1, arete_id=None, nid="111", cid="222") -> UpdateItem:
    note = anki_note(source_file=path, source_index=index)
    note.arete_id = arete_id
    return UpdateItem(
        source_file=path, source_index=index, new_nid=nid, new_cid=cid, ok=True, note=note
    )


# --- write-back target moved or gone ---


def test_a_card_that_vanished_during_sync_gets_no_ids_and_is_logged(vault, cache, caplog):
    path = write_note(vault, cards=[card(arete_id="arete_A"), card(arete_id="arete_B")])
    before = path.read_text(encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        VaultService(vault, cache).apply_updates([_update(path, index=1, arete_id="arete_gone")])
    assert path.read_text(encoding="utf-8") == before
    assert "arete_gone moved or vanished during sync" in caplog.text


def test_ids_follow_the_arete_id_when_the_card_moved(vault, cache):
    path = write_note(vault, cards=[card(arete_id="arete_A"), card(arete_id="arete_B")])
    # The sync saw B at position 1; by write-back time it is at position 2.
    VaultService(vault, cache).apply_updates([_update(path, index=1, arete_id="arete_B")])
    text = path.read_text(encoding="utf-8")
    first, second = text.split("arete_B")
    assert "111" not in first
    assert "111" in second
    assert "222" in second


def test_a_card_without_an_id_is_found_by_position(vault, cache):
    path = write_note(vault, cards=[card(arete_id=None), card(arete_id=None, front="Other")])
    VaultService(vault, cache).apply_updates([_update(path, index=2, arete_id=None)])
    text = path.read_text(encoding="utf-8")
    assert text.index("Other") < text.index("111")


def test_a_position_past_the_end_writes_nothing(vault, cache):
    path = write_note(vault, cards=[card(arete_id=None)])
    before = path.read_text(encoding="utf-8")
    VaultService(vault, cache).apply_updates([_update(path, index=5, arete_id=None)])
    assert path.read_text(encoding="utf-8") == before


def test_dry_run_write_back_leaves_the_file_alone(vault, cache, caplog):
    path = write_note(vault, cards=[card(arete_id="arete_A")])
    before = path.read_text(encoding="utf-8")
    with caplog.at_level(logging.INFO):
        VaultService(vault, cache).apply_updates([_update(path, arete_id="arete_A")], dry_run=True)
    assert path.read_text(encoding="utf-8") == before
    assert "Would write nid/cid" in caplog.text


# --- the stat cache fails ---


def test_scan_still_yields_the_file_when_the_cache_lookup_raises(vault, caplog):
    write_note(vault, "A.md")
    broken = MagicMock(spec=ContentCache)
    broken.get_file_meta_by_stat.side_effect = RuntimeError("db locked")
    with caplog.at_level(logging.WARNING):
        found = list(VaultService(vault, broken).scan_for_compatible_files())
    assert [p.name for p, _, _ in found] == ["A.md"]
    assert found[0][2] is True  # parsed fresh, not served from the cache
    assert "cache miss (error) for A.md: db locked" in caplog.text


def test_scan_completes_when_recording_plain_notes_fails(vault, caplog):
    (vault / "plain.md").write_text("# no cards here\n", encoding="utf-8")
    write_note(vault, "A.md")
    broken = MagicMock(spec=ContentCache)
    broken.get_file_meta_by_stat.return_value = None
    broken.set_file_metas.side_effect = RuntimeError("disk full")
    with caplog.at_level(logging.WARNING):
        found = list(VaultService(vault, broken).scan_for_compatible_files())
    assert [p.name for p, _, _ in found] == ["A.md"]
    assert "could not record 1 plain notes: disk full" in caplog.text


# --- record_synced ---


def test_record_synced_without_a_cache_does_nothing(vault):
    path = write_note(vault)
    VaultService(vault, None).record_synced([path])  # type: ignore[arg-type]


def test_record_synced_skips_a_file_whose_yaml_no_longer_parses(vault):
    good = write_note(vault, "Good.md")
    bad = vault / "Bad.md"
    bad.write_text("---\nkey: : value\n---\n", encoding="utf-8")
    cache = MagicMock(spec=ContentCache)
    VaultService(vault, cache).record_synced([bad, good])
    assert [c.args[0] for c in cache.set_file_meta.call_args_list] == [good]


def test_record_synced_logs_a_failure_and_still_records_the_rest(vault, caplog):
    first = write_note(vault, "First.md")
    second = write_note(vault, "Second.md")
    cache = MagicMock(spec=ContentCache)
    cache.set_file_meta.side_effect = [RuntimeError("db locked"), None]
    with caplog.at_level(logging.WARNING):
        VaultService(vault, cache).record_synced([first, second])
    assert "could not record First.md as synced: db locked" in caplog.text
    assert cache.set_file_meta.call_count == 2


# --- format_vault ---


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_format_vault_logs_an_unwritable_file_and_formats_the_others(vault, cache, caplog):
    messy = '---\narete: true\ndeck: D\ncards:\n  - Front: "A"\n    Back: "x\\ny"\n---\nbody\n'
    locked = vault / "Locked.md"
    open_ = vault / "Open.md"
    locked.write_text(messy, encoding="utf-8")
    open_.write_text(messy, encoding="utf-8")
    locked.chmod(0o444)
    try:
        with caplog.at_level(logging.ERROR):
            VaultService(vault, cache).format_vault()
    finally:
        locked.chmod(0o644)
    assert "formatting Locked.md" in caplog.text
    assert locked.read_text(encoding="utf-8") == messy
    assert "|-" in open_.read_text(encoding="utf-8")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_format_vault_count_excludes_files_that_could_not_be_written(vault, cache):
    messy = '---\narete: true\ndeck: D\ncards:\n  - Front: "A"\n    Back: "x\\ny"\n---\nbody\n'
    locked = vault / "Locked.md"
    locked.write_text(messy, encoding="utf-8")
    locked.chmod(0o444)
    try:
        count = VaultService(vault, cache).format_vault()
    finally:
        locked.chmod(0o644)
    assert count == 0
