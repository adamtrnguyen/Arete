"""Tests for arete.application.utils.common — to_list, sanitize, detect_anki_paths."""

import platform
from pathlib import Path
from unittest.mock import patch

from arete.application.utils.common import detect_anki_paths, sanitize, to_list


# ---------- to_list edge cases ----------


def test_to_list_with_none():
    """None returns empty list."""
    assert to_list(None) == []


def test_to_list_with_single_value():
    """Single value is wrapped in a list."""
    assert to_list("single") == ["single"]
    assert to_list(42) == ["42"]


def test_to_list_empty_list():
    """Empty list returns empty list."""
    assert to_list([]) == []


def test_to_list_mixed_types():
    """Mixed types in list are all converted to strings."""
    assert to_list([1, "two", 3.0, None]) == ["1", "two", "3.0", "None"]


def test_to_list_nested_list():
    """Nested lists are stringified (not flattened)."""
    result = to_list([[1, 2], [3]])
    assert len(result) == 2
    assert result[0] == "[1, 2]"


def test_to_list_boolean():
    """Boolean wraps as string in a list."""
    assert to_list(True) == ["True"]
    assert to_list(False) == ["False"]


def test_to_list_zero():
    """Zero wraps correctly (not confused with None/falsy)."""
    assert to_list(0) == ["0"]


def test_to_list_empty_string():
    """Empty string wraps correctly."""
    assert to_list("") == [""]


def test_to_list_path():
    """Path objects are stringified."""
    assert to_list(Path("/foo/bar")) == ["/foo/bar"]


# ---------- sanitize edge cases ----------


def test_sanitize_with_none():
    """None returns empty string."""
    assert sanitize(None) == ""


def test_sanitize_with_number():
    """Numbers are converted to strings."""
    assert sanitize(42) == "42"
    assert sanitize(3.14) == "3.14"


def test_sanitize_empty_string():
    """Empty string stays empty."""
    assert sanitize("") == ""


def test_sanitize_whitespace_only():
    """Whitespace-only string becomes empty after rstrip."""
    assert sanitize("   ") == ""


def test_sanitize_leading_whitespace_preserved():
    """Leading whitespace is preserved (only trailing is stripped)."""
    assert sanitize("  hello  ") == "  hello"


def test_sanitize_tabs():
    """Trailing tabs are stripped."""
    assert sanitize("hello\t\t") == "hello"


def test_sanitize_mixed_trailing():
    """Mixed trailing whitespace is stripped."""
    assert sanitize("hello \n \t") == "hello"


def test_sanitize_boolean():
    """Boolean is converted to string."""
    assert sanitize(True) == "True"
    assert sanitize(False) == "False"


def test_sanitize_list():
    """List is stringified."""
    assert sanitize([1, 2]) == "[1, 2]"


# ---------- detect_anki_paths edge cases ----------


def test_detect_anki_paths_darwin():
    """Anki path detection on macOS."""
    with patch.object(platform, "system", return_value="Darwin"):
        base, media = detect_anki_paths()
        assert base is not None
        assert str(base).replace("\\", "/").endswith("Library/Application Support/Anki2")
        assert str(media).replace("\\", "/").endswith("collection.media")


def test_detect_anki_paths_linux():
    """Anki path detection on Linux (non-WSL)."""
    with (
        patch.object(platform, "system", return_value="Linux"),
        patch.object(
            platform, "uname", return_value=type("obj", (), {"release": "5.15.0-generic"})()
        ),
    ):
        base, media = detect_anki_paths()
        assert base is not None
        assert str(base).replace("\\", "/").endswith(".local/share/Anki2")


def test_detect_anki_paths_unknown_os():
    """Unknown OS returns None base and Path('.') media."""
    with patch.object(platform, "system", return_value="UnknownOS"):
        base, media = detect_anki_paths()
        assert base is None
        assert media == Path(".")


def test_detect_anki_paths_windows_no_profiles(tmp_path):
    """Windows with Anki2 dir but no profiles falls back to User 1."""
    with (
        patch.object(platform, "system", return_value="Windows"),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "iterdir", return_value=[]),
    ):
        base, media = detect_anki_paths()
        assert base is not None
        assert "User 1" in str(media).replace("\\", "/")


def test_detect_anki_paths_wsl_with_users(tmp_path):
    """WSL with /mnt/c/Users existing picks first non-system user."""
    user_dir = tmp_path / "TestUser"
    user_dir.mkdir()

    with (
        patch.object(platform, "system", return_value="Linux"),
        patch.object(
            platform,
            "uname",
            return_value=type("obj", (), {"release": "5.15.0-microsoft-standard-WSL2"})(),
        ),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "iterdir", return_value=[user_dir]),
        patch.object(Path, "is_dir", return_value=True),
    ):
        base, media = detect_anki_paths()
        # Should use the WSL path via /mnt/c/Users
        assert base is not None
