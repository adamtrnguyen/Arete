from unittest.mock import patch

from arete.application.utils.fs import file_md5, iter_markdown_files


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
    """ValueError from relative_to doesn't crash iteration."""
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
