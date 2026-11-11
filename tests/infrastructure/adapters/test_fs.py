import os
import signal
import stat
import subprocess
import sys
from unittest.mock import patch

import pytest

from arete.application.utils.fs import file_md5, iter_markdown_files, replace_file


def test_iter_markdown_files_simple(tmp_path):
    """Verify scanning finds .md files and ignores others."""
    # Setup
    (tmp_path / "note.md").touch()
    (tmp_path / "other.txt").touch()
    (tmp_path / "image.png").touch()

    files = list(iter_markdown_files(tmp_path))

    assert len(files) == 1
    assert files[0].name == "note.md"


def test_iter_markdown_files_nested(tmp_path):
    """Verify recursive scanning."""
    d = tmp_path / "subdir"
    d.mkdir()
    (d / "nested.md").touch()

    files = list(iter_markdown_files(tmp_path))
    assert len(files) == 1
    assert files[0].name == "nested.md"


def test_iter_markdown_files_excludes(tmp_path):
    """Verify that standard ignored directories and hidden folders are skipped."""
    # 1. .git (explicit exclude)
    git = tmp_path / ".git"
    git.mkdir()
    (git / "ignore_git.md").touch()

    # 2. .obsidian (explicit exclude)
    obs = tmp_path / ".obsidian"
    obs.mkdir()
    (obs / "ignore_obs.md").touch()

    # 3. .trash (explicit exclude)
    trash = tmp_path / ".trash"
    trash.mkdir()
    (trash / "ignore_trash.md").touch()

    # 4. Custom hidden folder (implicit exclude by starting with .)
    hidden = tmp_path / ".my_hidden_folder"
    hidden.mkdir()
    (hidden / "ignore_hidden.md").touch()

    # 5. Valid folder
    ok = tmp_path / "Notes"
    ok.mkdir()
    (ok / "valid.md").touch()

    files = {p.name for p in iter_markdown_files(tmp_path)}

    assert "valid.md" in files
    assert "ignore_git.md" not in files
    assert "ignore_obs.md" not in files
    assert "ignore_trash.md" not in files
    assert "ignore_hidden.md" not in files


def test_iter_markdown_files_nested_excludes(tmp_path):
    """Verify exclusions work even deep in the tree."""
    # Structure: /Notes/Sub/.git/oops.md

    sub = tmp_path / "Notes/Sub"
    sub.mkdir(parents=True)

    (sub / "good.md").touch()

    git_nested = sub / ".git"
    git_nested.mkdir()
    (git_nested / "bad.md").touch()

    files = list(iter_markdown_files(tmp_path))

    assert len(files) == 1
    assert files[0].name == "good.md"


def test_iter_single_file(tmp_path):
    """When given a single file path, return just that file."""
    f = tmp_path / "test.md"
    f.touch()
    files = list(iter_markdown_files(f))
    assert files == [f]


def test_iter_value_error_fallback(tmp_path):
    """A ValueError from relative_to is handled gracefully, never crashing.

    rglob always yields paths under root, so relative_to never raises in
    practice; the except branch is purely defensive. When it does trip we fail
    open — the markdown file is still yielded rather than silently dropped, we
    just can't apply the skip-dir filter to it.
    """
    with patch("pathlib.Path.relative_to", side_effect=ValueError("Mismatch")):
        d = tmp_path / "dir"
        d.mkdir()
        f = d / "test.md"
        f.touch()
        files = list(iter_markdown_files(d))
        assert files == [f]


def test_file_md5(tmp_path):
    """file_md5 returns a 32-char hex digest that changes with content."""
    f = tmp_path / "test.txt"
    f.write_text("hello world")
    h1 = file_md5(f)
    assert len(h1) == 32

    f.write_text("hello world!")
    h2 = file_md5(f)
    assert h1 != h2


# ---------------------------------------------------------------------------
# replace_file: every write of a vault note
# ---------------------------------------------------------------------------

NOTE = b"---\r\narete: true\r\ncards: []\r\n---\r\n\r\nBody \xc3\xa9.\r\n"


def _leftovers(folder):
    return sorted(p.name for p in folder.iterdir() if p.name.endswith(".tmp"))


def test_the_new_bytes_replace_the_note_exactly(tmp_path):
    note = tmp_path / "Bayes.md"
    note.write_bytes(b"old")

    replace_file(note, NOTE)

    assert note.read_bytes() == NOTE, "CRLF and UTF-8 kept byte for byte"
    assert _leftovers(tmp_path) == []


def test_a_write_that_fails_midway_leaves_the_note_as_it_was(tmp_path, monkeypatch):
    note = tmp_path / "Bayes.md"
    note.write_bytes(NOTE)

    def broken_fsync(_fd):
        raise OSError("disk full")

    monkeypatch.setattr("arete.application.utils.fs.os.fsync", broken_fsync)
    with pytest.raises(OSError, match="disk full"):
        replace_file(note, b"half")

    assert note.read_bytes() == NOTE
    assert _leftovers(tmp_path) == [], "the temp file is removed"


def test_the_notes_permissions_are_kept(tmp_path):
    note = tmp_path / "Bayes.md"
    note.write_bytes(NOTE)
    os.chmod(note, 0o640)

    replace_file(note, b"new")

    assert stat.S_IMODE(note.stat().st_mode) == 0o640


@pytest.mark.skipif(os.name == "posix" and os.geteuid() == 0, reason="root ignores file modes")
def test_a_read_only_note_is_refused_and_left_alone(tmp_path):
    """Replacing it would succeed in a writable folder; writing it in place would not."""
    note = tmp_path / "Bayes.md"
    note.write_bytes(NOTE)
    os.chmod(note, 0o444)
    try:
        with pytest.raises(PermissionError):
            replace_file(note, b"new")
    finally:
        os.chmod(note, 0o644)

    assert note.read_bytes() == NOTE
    assert _leftovers(tmp_path) == []


def test_a_symlinked_note_is_written_at_its_target(tmp_path):
    target = tmp_path / "real.md"
    target.write_bytes(NOTE)
    link = tmp_path / "Bayes.md"
    link.symlink_to(target)

    replace_file(link, b"new")

    assert link.is_symlink() and target.read_bytes() == b"new"


KILLED_MID_WRITE = """
import os, sys, time
from pathlib import Path
import arete.application.utils.fs as fs

def slow_fsync(fd):
    print("writing", flush=True)
    time.sleep(60)

fs.os.fsync = slow_fsync
fs.replace_file(Path(sys.argv[1]), b"x" * 100000)
"""


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL")
def test_a_kill_in_the_middle_of_the_write_leaves_the_note_whole(tmp_path):
    note = tmp_path / "Bayes.md"
    note.write_bytes(NOTE)
    child = subprocess.Popen(
        [sys.executable, "-c", KILLED_MID_WRITE, str(note)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "writing"
    finally:
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=10)

    assert note.read_bytes() == NOTE
