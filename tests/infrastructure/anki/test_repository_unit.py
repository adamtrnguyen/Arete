import pickle
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from arete.infrastructure.anki.repository import AnkiRepository


@pytest.fixture
def mock_col():
    with patch("arete.infrastructure.anki.repository.Collection") as mock:
        yield mock


@pytest.fixture
def repo():
    return AnkiRepository(base_path=Path("/tmp/anki"))


def test_resolve_collection_path_explicit_profile(repo):
    repo.profile_name = "User1"
    # Mock existence of prefs db
    with patch.object(Path, "exists", return_value=True):
        path = repo._resolve_collection_path()
    assert path == Path("/tmp/anki") / "User1" / "collection.anki2"


@patch("arete.infrastructure.anki.repository.sqlite3")
def test_resolve_collection_path_auto(mock_sqlite, repo):
    # Mock prefs21.db
    mock_conn = MagicMock()
    mock_sqlite.connect.return_value = mock_conn

    # Mock global profile data
    mock_cursor = MagicMock()
    mock_conn.execute.return_value = mock_cursor

    data = {"last_loaded_profile_name": "UserAuto"}
    blob = pickle.dumps(data)
    mock_cursor.fetchone.return_value = (blob,)

    with patch.object(Path, "exists", return_value=True):
        path = repo._resolve_collection_path()

    assert path == Path("/tmp/anki") / "UserAuto" / "collection.anki2"
    assert repo.profile_name == "UserAuto"


def test_context_manager(mock_col, repo):
    # Setup mock collection instance
    mock_instance = MagicMock()
    mock_col.return_value = mock_instance  # When Collection() is called, return this

    with patch.object(repo, "_resolve_collection_path", return_value=Path("/tmp/c.anki2")):
        with repo as r:
            assert r.col is mock_instance
            mock_col.assert_called_with(str(Path("/tmp/c.anki2")))

        # Exit
        mock_instance.close.assert_called_once()
        assert r.col is None


def test_a_collection_another_process_holds_names_the_way_out(tmp_path):
    """As with Anki open: the hint says Anki holds it, and how to reach it; no guess at why."""
    import subprocess
    import sys

    from tests.e2e.conftest import PROFILE, make_anki_base

    base = make_anki_base(tmp_path / "anki")
    path = base / PROFILE / "collection.anki2"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys\nfrom anki.collection import Collection\n"
            f"c = Collection({str(path)!r})\nprint('open', flush=True)\nsys.stdin.read()\n",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "open"
        with pytest.raises(OSError) as refused:
            with AnkiRepository(base):
                pass
    finally:
        holder.kill()
        holder.wait(timeout=10)

    text = str(refused.value)
    assert "Anki is running and holds the collection" in text
    assert "backend auto or ankiconnect" in text
    assert "did not answer" not in text
