"""Every write of a vault note replaces it whole: a write that fails leaves it as it was.

The writers are the id write-back (`write_exact`: new Arete ids, nids and cids), `vault
format` and `vault fix`. Each goes through `replace_file`, which flushes and fsyncs a temp
file before it replaces the note; here fsync fails, which a write in place never reaches,
so a writer that bypassed it would leave the note changed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from arete.application.sync.id_writeback import write_exact
from arete.application.sync.vault_service import VaultService
from arete.interface.cli import app

NEEDS_ID = (
    "---\r\narete: true\r\ndeck: D\r\ncards:\r\n- Front: Q\r\n  Back: A\r\n---\r\n\r\nBody.\r\n"
)
TABBED = "---\narete: true\ndeck: D\ncards:\n\t- Front: Q\n\t  Back: A\n---\n"
QUOTED = '---\narete: true\ndeck: D\ncards:\n  - Front: "A"\n    Back: "line1\\nline2"\n---\nbody\n'


@pytest.fixture
def failing_fsync(monkeypatch):
    def fsync(_fd):
        raise OSError("disk full")

    monkeypatch.setattr("arete.application.utils.fs.os.fsync", fsync)


def _unchanged(note: Path, before: bytes) -> None:
    assert note.read_bytes() == before
    assert not [p for p in note.parent.iterdir() if p.name.endswith(".tmp")]


def test_write_exact(tmp_path: Path, failing_fsync):
    note = tmp_path / "n.md"
    note.write_bytes(NEEDS_ID.encode())

    with pytest.raises(OSError, match="disk full"):
        write_exact(note, "replaced")

    _unchanged(note, NEEDS_ID.encode())


def test_assigning_ids(tmp_path: Path, failing_fsync):
    note = tmp_path / "n.md"
    note.write_bytes(NEEDS_ID.encode())

    with pytest.raises(OSError, match="disk full"):
        VaultService(tmp_path, MagicMock()).assign_ids(note)

    _unchanged(note, NEEDS_ID.encode())


def test_vault_format(tmp_path: Path, failing_fsync, caplog):
    note = tmp_path / "n.md"
    note.write_bytes(QUOTED.encode())
    cache = MagicMock()
    cache.get_file_meta_by_stat.return_value = None

    assert VaultService(tmp_path, cache, ignore_cache=True).format_vault() == 0

    assert "formatting n.md: disk full" in caplog.text, "the write was tried, and failed"
    _unchanged(note, QUOTED.encode())


def test_vault_fix(tmp_path: Path, failing_fsync):
    note = tmp_path / "n.md"
    note.write_bytes(TABBED.encode())

    result = CliRunner().invoke(app, ["vault", "fix", str(note)])

    assert result.exit_code != 0
    _unchanged(note, TABBED.encode())
