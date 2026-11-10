import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Fixture to safely patch Anki modules only for this test module context
@pytest.fixture(scope="module", autouse=True)
def mock_anki_modules():
    anki_mock = MagicMock()
    with patch.dict(
        sys.modules,
        {
            "anki": anki_mock,
            "anki.collection": MagicMock(),
            "anki.models": MagicMock(),
            "anki.decks": MagicMock(),
            "anki.notes": MagicMock(),
        },
    ):
        # We must reload the repository module to ensure it picks up the mocks
        if "arete.infrastructure.anki.repository" in sys.modules:
            importlib.reload(sys.modules["arete.infrastructure.anki.repository"])
        yield

    # After tests, we should reload the real module if possible, or remove it so it's re-imported cleanly
    if "arete.infrastructure.anki.repository" in sys.modules:
        importlib.reload(sys.modules["arete.infrastructure.anki.repository"])


from arete.infrastructure.anki.repository import AnkiRepository  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    repo = AnkiRepository(base_path=tmp_path)
    return repo


def test_init_raises():
    with pytest.raises(ValueError):
        AnkiRepository(base_path=None)


def test_resolve_collection_path_profile_set(repo, tmp_path):
    (tmp_path / "prefs21.db").touch()
    repo.profile_name = "User1"
    path = repo._resolve_collection_path()
    assert path == tmp_path / "User1" / "collection.anki2"


def test_resolve_collection_path_auto(repo, tmp_path):
    prefs = tmp_path / "prefs21.db"
    prefs.touch()

    with patch("sqlite3.connect") as mock_conn:
        mock_cursor = mock_conn.return_value.execute.return_value
        import pickle

        data = pickle.dumps({"last_loaded_profile_name": "AutoUser"})
        mock_cursor.fetchone.return_value = [data]

        path = repo._resolve_collection_path()
        assert path == tmp_path / "AutoUser" / "collection.anki2"
        assert repo.profile_name == "AutoUser"


def test_resolve_collection_path_missing_prefs(repo, tmp_path):
    # base_path exists but prefs21.db doesn't
    with pytest.raises(FileNotFoundError, match="Anki prefs not found"):
        repo._resolve_collection_path()


def test_resolve_collection_path_no_global_profile(repo, tmp_path):
    (tmp_path / "prefs21.db").touch()
    with patch("sqlite3.connect") as mock_conn:
        mock_cursor = mock_conn.return_value.execute.return_value
        mock_cursor.fetchone.return_value = None  # No global profile
        with pytest.raises(ValueError, match="Could not read global profile data"):
            repo._resolve_collection_path()


def test_resolve_collection_path_no_last_profile(repo, tmp_path):
    (tmp_path / "prefs21.db").touch()
    with patch("sqlite3.connect") as mock_conn:
        mock_cursor = mock_conn.return_value.execute.return_value
        import pickle

        data = pickle.dumps({"other": "data"})  # No last_loaded_profile_name
        mock_cursor.fetchone.return_value = [data]
        with pytest.raises(ValueError, match="Could not determine last loaded profile"):
            repo._resolve_collection_path()


def test_enter_error(repo):
    with patch.object(repo, "_resolve_collection_path", return_value=Path("/tmp/col.anki2")):
        with patch(
            "arete.infrastructure.anki.repository.Collection",
            side_effect=Exception("Failed to open"),
        ):
            with pytest.raises(OSError, match="Could not open Anki collection"):
                with repo:
                    pass


def test_runtime_errors_when_closed(repo):
    with pytest.raises(RuntimeError, match="Collection not open"):
        repo.find_notes("query")


def test_enter_exit(repo):
    with patch.object(repo, "_resolve_collection_path", return_value=Path("/tmp/col.anki2")):
        # We assume Collection is patched by the fixture
        # But we need to assert on it. The fixture patched sys.modules["anki.collection"].Collection?
        # No, the fixture patches `sys.modules["anki.collection"] = MagicMock()`.
        # So `from anki.collection import Collection` means `Collection` is `sys.modules["anki.collection"].Collection`.

        # We need to access that mock to assert calls.
        # It's safest to patch it again inside the test or use the imported symbol.

        # Note: MockCol is the Mock object if reload worked.

        with repo as r:
            assert r.col is not None
            captured_col = r.col

        captured_col.close.assert_called_once()
        assert repo.col is None
