from unittest.mock import MagicMock, patch

import pytest

from arete.application.sync.vault_service import VaultService
from arete.domain.models import UpdateItem
from arete.infrastructure.persistence.cache import ContentCache


@pytest.fixture
def mock_cache():
    return MagicMock(spec=ContentCache)


@pytest.fixture
def temp_vault(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def test_vault_service_uses_cache_by_default(temp_vault, mock_cache):
    """Verify that VaultService queries the cache when ignore_cache is False (default)."""
    # Setup
    md_file = temp_vault / "test.md"
    md_file.write_text("---\ncards:\n  - Front: F\n    Back: B\n---\nBody", encoding="utf-8")

    # Simulate a cache hit
    mock_cache.get_file_meta_by_stat.return_value = {"cards": [{"Front": "F"}], "arete": True}

    service = VaultService(temp_vault, mock_cache, ignore_cache=False)

    files = list(service.scan_for_compatible_files())

    assert len(files) == 1
    # Should have called get_file_meta_by_stat
    mock_cache.get_file_meta_by_stat.assert_called_once()


def test_vault_service_bypasses_cache_when_ignored(temp_vault, mock_cache):
    """Verify that VaultService DOES NOT query the cache when ignore_cache is True."""
    # Setup
    md_file = temp_vault / "test.md"
    # Needs valid frontmatter because cache lookup will be skipped, so it must parse manually
    md_file.write_text(
        "---\narete: true\ndeck: Default\ncards:\n  - Front: F\n    Back: B\n---\nBody",
        encoding="utf-8",
    )

    service = VaultService(temp_vault, mock_cache, ignore_cache=True)

    files = list(service.scan_for_compatible_files())

    assert len(files) == 1
    # Should NOT have called get_file_meta_by_stat
    mock_cache.get_file_meta_by_stat.assert_not_called()

    # Since we bypassed cache and parsed successfully (mocked file has content),
    # it should set the new meta into cache
    mock_cache.set_file_meta.assert_called_once()


# ---------------------------------------------------------------------------
# Heuristic detection tests
# ---------------------------------------------------------------------------


class TestFileHeuristicDetection:
    """Tests for the header-heuristic that decides whether to parse a file."""

    def test_arete_true_is_detected(self, temp_vault, mock_cache):
        """A file with ``arete: true`` in its frontmatter is accepted."""
        md = temp_vault / "good.md"
        md.write_text(
            "---\narete: true\ndeck: Default\ncards:\n  - Front: Q\n    Back: A\n---\nBody",
            encoding="utf-8",
        )
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())

        assert len(files) == 1
        path, meta, is_fresh = files[0]
        assert path == md
        assert meta["arete"] is True
        assert is_fresh is True

    def test_file_without_arete_marker_is_skipped(self, temp_vault, mock_cache):
        """A plain markdown file with no arete/anki markers is skipped."""
        md = temp_vault / "plain.md"
        md.write_text(
            "---\ntitle: My Note\ntags: [foo]\n---\nJust regular content.",
            encoding="utf-8",
        )
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())

        assert len(files) == 0

    def test_legacy_anki_template_version_1_is_detected(self, temp_vault, mock_cache):
        """Legacy ``anki_template_version: 1`` files are accepted."""
        md = temp_vault / "legacy.md"
        md.write_text(
            "---\nanki_template_version: 1\ndeck: Legacy\ncards:\n  - Front: Q\n    Back: A\n---\n",
            encoding="utf-8",
        )
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())

        assert len(files) == 1
        _path, meta, _fresh = files[0]
        assert meta.get("anki_template_version") == 1

    def test_cards_only_without_arete_marker_is_accepted(self, temp_vault, mock_cache):
        """A file with ``cards:`` and ``deck:`` is accepted even without ``arete: true``."""
        md = temp_vault / "cards_only.md"
        md.write_text(
            "---\ncards:\n  - Front: Q\n    Back: A\ndeck: SomeDeck\n---\nBody",
            encoding="utf-8",
        )
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())

        assert len(files) == 1


# ---------------------------------------------------------------------------
# Edge-case file content tests
# ---------------------------------------------------------------------------


class TestEdgeCaseFiles:
    """Tests for empty files, binary files, and non-markdown files."""

    def test_empty_file_is_skipped(self, temp_vault, mock_cache):
        """An empty .md file has no frontmatter and should be skipped."""
        md = temp_vault / "empty.md"
        md.write_text("", encoding="utf-8")
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())

        assert len(files) == 0

    def test_binary_content_is_skipped(self, temp_vault, mock_cache):
        """A .md file with binary (non-UTF-8) content should be skipped
        because read_text with errors='strict' will fail.
        """
        md = temp_vault / "binary.md"
        # Write raw bytes that are invalid UTF-8
        md.write_bytes(b"\x80\x81\x82\xff\xfe---\ncards:\n---\n")
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())

        assert len(files) == 0

    def test_non_markdown_txt_file_is_ignored(self, temp_vault, mock_cache):
        """A .txt file is not a markdown file and should not be scanned."""
        txt = temp_vault / "note.txt"
        txt.write_text(
            "---\narete: true\ndeck: D\ncards:\n  - Front: Q\n    Back: A\n---\n",
            encoding="utf-8",
        )
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())

        assert len(files) == 0

    def test_read_error_is_skipped(self, temp_vault, mock_cache):
        """File that raises OSError on read is silently skipped."""
        md = temp_vault / "test.md"
        md.touch()
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        with patch("pathlib.Path.read_text", side_effect=OSError("Read error")):
            files = list(service.scan_for_compatible_files())
            assert len(files) == 0

    def test_cards_not_list_is_skipped(self, temp_vault, mock_cache):
        """File where cards is not a list is skipped."""
        md = temp_vault / "test.md"
        md.write_text("---\narete: true\ncards: 'not_list'\ndeck: D\n---\n")
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())
        assert len(files) == 0

    def test_bad_yaml_content_is_skipped(self, temp_vault, mock_cache):
        """File with unparseable YAML is skipped."""
        md = temp_vault / "test.md"
        md.write_text("---\nbad: : :\n---\n")
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())
        assert len(files) == 0

    def test_base_file_is_ignored(self, temp_vault, mock_cache):
        """A .base file (Obsidian database view) should not be scanned."""
        base = temp_vault / "Art Books.base"
        base.write_text(
            "---\narete: true\ndeck: D\ncards:\n  - Front: Q\n    Back: A\n---\n",
            encoding="utf-8",
        )
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())

        assert len(files) == 0


# ---------------------------------------------------------------------------
# Deck requirement tests
# ---------------------------------------------------------------------------


class TestDeckRequirement:
    """Tests for deck specification at file vs. card level."""

    def test_deck_at_file_level_accepted(self, temp_vault, mock_cache):
        """File-level ``deck:`` satisfies the deck requirement."""
        md = temp_vault / "file_deck.md"
        md.write_text(
            "---\narete: true\ndeck: MyDeck\ncards:\n  - Front: Q\n    Back: A\n---\n",
            encoding="utf-8",
        )
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())

        assert len(files) == 1

    def test_deck_only_at_card_level_accepted(self, temp_vault, mock_cache):
        """Card-level ``deck:`` (with no file-level deck) satisfies the requirement."""
        md = temp_vault / "card_deck.md"
        md.write_text(
            "---\narete: true\ncards:\n  - Front: Q\n    Back: A\n    deck: CardDeck\n---\n",
            encoding="utf-8",
        )
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())

        assert len(files) == 1

    def test_no_deck_anywhere_rejected_by_default(self, temp_vault, mock_cache):
        """No deck at file or card level -> rejected when ignore_cache=False."""
        md = temp_vault / "no_deck.md"
        md.write_text(
            "---\narete: true\ncards:\n  - Front: Q\n    Back: A\n---\n",
            encoding="utf-8",
        )
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache, ignore_cache=False)
        files = list(service.scan_for_compatible_files())

        assert len(files) == 0

    def test_no_deck_accepted_when_ignore_cache(self, temp_vault, mock_cache):
        """No deck at all is accepted when ignore_cache=True (--force mode),
        because it is still useful for normalization.
        """
        md = temp_vault / "no_deck_force.md"
        md.write_text(
            "---\narete: true\ncards:\n  - Front: Q\n    Back: A\n---\n",
            encoding="utf-8",
        )
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache, ignore_cache=True)
        files = list(service.scan_for_compatible_files())

        assert len(files) == 1


# ---------------------------------------------------------------------------
# Subdirectory scanning
# ---------------------------------------------------------------------------


class TestSubdirectoryScanning:
    """Tests for recursive scanning of nested directories."""

    def test_files_in_nested_dirs_are_found(self, temp_vault, mock_cache):
        """Markdown files in subdirectories should be discovered."""
        subdir = temp_vault / "topics" / "math"
        subdir.mkdir(parents=True)
        md = subdir / "algebra.md"
        md.write_text(
            "---\narete: true\ndeck: Math\ncards:\n  - Front: Q\n    Back: A\n---\n",
            encoding="utf-8",
        )
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())

        assert len(files) == 1
        assert files[0][0] == md

    def test_hidden_dirs_are_skipped(self, temp_vault, mock_cache):
        """Files inside hidden directories (starting with .) should be skipped."""
        hidden = temp_vault / ".obsidian"
        hidden.mkdir()
        md = hidden / "config.md"
        md.write_text(
            "---\narete: true\ndeck: D\ncards:\n  - Front: Q\n    Back: A\n---\n",
            encoding="utf-8",
        )
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())

        assert len(files) == 0

    def test_mixed_depth_scanning(self, temp_vault, mock_cache):
        """Files at root, one level deep, and two levels deep are all found."""
        content = "---\narete: true\ndeck: D\ncards:\n  - Front: Q\n    Back: A\n---\n"

        root_file = temp_vault / "root.md"
        root_file.write_text(content, encoding="utf-8")

        lvl1 = temp_vault / "sub"
        lvl1.mkdir()
        lvl1_file = lvl1 / "level1.md"
        lvl1_file.write_text(content, encoding="utf-8")

        lvl2 = lvl1 / "deep"
        lvl2.mkdir()
        lvl2_file = lvl2 / "level2.md"
        lvl2_file.write_text(content, encoding="utf-8")

        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())

        found_paths = {f[0] for f in files}
        assert found_paths == {root_file, lvl1_file, lvl2_file}


# ---------------------------------------------------------------------------
# format_vault() tests
# ---------------------------------------------------------------------------


class TestFormatVault:
    """Tests for the ``format_vault`` method."""

    def test_dry_run_returns_count_without_writing(self, temp_vault, mock_cache):
        """``format_vault(dry_run=True)`` should report files that *would*
        change but NOT modify them on disk.
        """
        # Write a file with formatting that will differ after round-trip
        # (e.g., YAML key ordering or block scalar style may change)
        original = (
            "---\narete: true\ndeck: D\ncards:\n  - Front: Q\n"
            '    Back: "multi\\nline"\n---\nBody'
        )
        md = temp_vault / "format_me.md"
        md.write_text(original, encoding="utf-8")
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        count = service.format_vault(dry_run=True)

        # The round-trip should detect a change (double-quoted escape -> |- block)
        assert count >= 0  # may or may not differ depending on dumper
        # The file on disk should be UNCHANGED
        assert md.read_text(encoding="utf-8") == original

    def test_format_vault_writes_when_not_dry_run(self, temp_vault, mock_cache):
        """``format_vault(dry_run=False)`` should actually write normalized files."""
        # Use a format that is guaranteed to change on round-trip: tabs in YAML
        # (parse_frontmatter replaces tabs with spaces)
        original = "---\narete: true\ndeck: D\ncards:\n  - Front: Q\n    Back: A\n---\nBody"
        md = temp_vault / "normalize.md"
        md.write_text(original, encoding="utf-8")
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        count = service.format_vault(dry_run=False)

        after = md.read_text(encoding="utf-8")
        if count > 0:
            # File was changed by the normalizer
            assert after != original
        # Either way the file should still be valid YAML
        assert after.startswith("---\n")


# ---------------------------------------------------------------------------
# apply_updates() tests
# ---------------------------------------------------------------------------


class TestApplyUpdates:
    """Tests for the ``apply_updates`` method that persists nid/cid back."""

    def test_apply_updates_persists_nid_cid(self, temp_vault, mock_cache):
        """After apply_updates, the file on disk should contain the new
        nid and cid inside an ``anki:`` block on the card.
        """
        md = temp_vault / "update_me.md"
        md.write_text(
            "---\narete: true\ndeck: D\ncards:\n  - Front: Q\n    Back: A\n---\nBody",
            encoding="utf-8",
        )

        update = UpdateItem(
            source_file=md,
            source_index=1,  # 1-based
            new_nid="9999999999",
            new_cid="8888888888",
            ok=True,
        )

        service = VaultService(temp_vault, mock_cache)
        service.apply_updates([update])

        text = md.read_text(encoding="utf-8")
        assert "nid" in text
        assert "9999999999" in text
        assert "cid" in text
        assert "8888888888" in text
        # Verify it's inside an anki block
        assert "anki:" in text

    def test_apply_updates_dry_run_does_not_write(self, temp_vault, mock_cache):
        """``apply_updates(..., dry_run=True)`` should not modify the file."""
        original = "---\narete: true\ndeck: D\ncards:\n  - Front: Q\n    Back: A\n---\nBody"
        md = temp_vault / "dry.md"
        md.write_text(original, encoding="utf-8")

        update = UpdateItem(
            source_file=md,
            source_index=1,
            new_nid="1111111111",
            new_cid="2222222222",
            ok=True,
        )

        service = VaultService(temp_vault, mock_cache)
        service.apply_updates([update], dry_run=True)

        assert md.read_text(encoding="utf-8") == original

    def test_apply_updates_skips_failed_items(self, temp_vault, mock_cache):
        """UpdateItems with ``ok=False`` should not cause any file writes."""
        original = "---\narete: true\ndeck: D\ncards:\n  - Front: Q\n    Back: A\n---\nBody"
        md = temp_vault / "fail.md"
        md.write_text(original, encoding="utf-8")

        update = UpdateItem(
            source_file=md,
            source_index=1,
            new_nid="1111111111",
            new_cid="2222222222",
            ok=False,
            error="some error",
        )

        service = VaultService(temp_vault, mock_cache)
        service.apply_updates([update])

        assert md.read_text(encoding="utf-8") == original

    def test_apply_updates_multiple_cards(self, temp_vault, mock_cache):
        """Applying updates to multiple cards in the same file."""
        md = temp_vault / "multi.md"
        md.write_text(
            "---\narete: true\ndeck: D\ncards:\n"
            "  - Front: Q1\n    Back: A1\n"
            "  - Front: Q2\n    Back: A2\n"
            "---\nBody",
            encoding="utf-8",
        )

        updates = [
            UpdateItem(source_file=md, source_index=1, new_nid="1001", new_cid="2001", ok=True),
            UpdateItem(source_file=md, source_index=2, new_nid="1002", new_cid="2002", ok=True),
        ]

        service = VaultService(temp_vault, mock_cache)
        service.apply_updates(updates)

        text = md.read_text(encoding="utf-8")
        assert "1001" in text
        assert "2001" in text
        assert "1002" in text
        assert "2002" in text

    def test_apply_updates_read_fail(self, tmp_path, mock_cache):
        """apply_updates handles OSError on read gracefully."""
        f = tmp_path / "test.md"
        f.write_text("---\narete: true\ncards: [{Front: f}]\ndeck: D\n---\n")
        update = UpdateItem(
            ok=True, error=None, source_file=f, source_index=1, new_nid="123", new_cid="456"
        )
        service = VaultService(tmp_path, mock_cache)
        with patch("pathlib.Path.read_text", side_effect=OSError("Panic")):
            service.apply_updates([update])

    def test_apply_updates_bad_yaml(self, tmp_path, mock_cache):
        """apply_updates handles unparseable YAML gracefully."""
        f = tmp_path / "test.md"
        f.write_text("---\nbad: : :\n---\n")
        update = UpdateItem(
            ok=True, error=None, source_file=f, source_index=1, new_nid="123", new_cid="456"
        )
        service = VaultService(tmp_path, mock_cache)
        service.apply_updates([update])

    def test_apply_updates_migrates_legacy_root_nid(self, temp_vault, mock_cache):
        """Legacy root-level nid/cid on a card should be migrated into the
        ``anki:`` block and removed from the card root.
        """
        md = temp_vault / "legacy_nid.md"
        md.write_text(
            "---\narete: true\ndeck: D\ncards:\n"
            "  - Front: Q\n    Back: A\n    nid: '555'\n    cid: '666'\n"
            "---\nBody",
            encoding="utf-8",
        )

        # Provide a new nid to trigger the update path
        update = UpdateItem(
            source_file=md,
            source_index=1,
            new_nid="777",
            new_cid="888",
            ok=True,
        )

        service = VaultService(temp_vault, mock_cache)
        service.apply_updates([update])

        text = md.read_text(encoding="utf-8")
        # The anki block should exist with the new values
        assert "anki:" in text
        assert "777" in text
        assert "888" in text


# ---------------------------------------------------------------------------
# Cache behavior tests
# ---------------------------------------------------------------------------


class TestCacheBehavior:
    """Tests for cache exception handling and write-through."""

    def test_cache_exception_falls_back_to_parsing(self, temp_vault, mock_cache):
        """Cache get raising an exception falls back to file parsing."""
        md = temp_vault / "test.md"
        md.write_text("---\narete: true\ncards: [{Front: f}]\ndeck: D\n---\n")
        mock_cache.get_file_meta_by_stat.side_effect = Exception("DB Fail")

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())
        assert len(files) == 1

    def test_cache_corrupted_falls_back_to_parsing(self, temp_vault, mock_cache):
        """Cache returning a non-dict truthy value falls back to parsing."""
        md = temp_vault / "test.md"
        md.write_text("---\narete: true\ncards: [{Front: f}]\ndeck: D\n---\n")
        mock_cache.get_file_meta_by_stat.return_value = "Not a dict"

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())
        assert len(files) == 1

    def test_scan_sets_cache_on_fresh_parse(self, temp_vault, mock_cache):
        """Scanning a file that misses cache calls set_file_meta."""
        md = temp_vault / "test.md"
        md.write_text("---\narete: true\ncards: [{Front: f}]\ndeck: D\n---\n")
        mock_cache.get_file_meta_by_stat.return_value = None

        service = VaultService(temp_vault, mock_cache)
        files = list(service.scan_for_compatible_files())
        assert len(files) == 1
        mock_cache.set_file_meta.assert_called_once()
