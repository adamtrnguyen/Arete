import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

# --- Isolation: no test may touch the user's real ~/.config/arete ---
# arete's defaults (cache.db, logs/, config.toml) all hang off Path.home(). On 2026-09-07 a
# pytest run wiped the real cache and wrote run logs into ~/.config/arete/logs.

_REAL_ARETE_CONFIG = Path.home() / ".config" / "arete"

# --- Isolation: no test may touch the user's real Anki collection ---
# detect_anki_paths() in application/utils/common.py builds the collection path from
# Path.home(), so the _isolated_home fixture below already redirects it. This is the
# belt to that fixture's braces: if a test ever resolves the real path anyway, the
# session fails loudly instead of writing notes into a live deck.
#
# It has happened. On 2026-03-03 and 2026-03-05 an integration run wrote four fixture
# notes ("Hello Integration"/"World", "Healing Candidate"/"Same Back") into a real
# collection under a deck named IntegrationTest. The dates come from the arete ULIDs
# those notes carried. Nothing caught it for six months.

_REAL_ANKI_BASE = Path.home() / "Library/Application Support/Anki2"  # macOS
if not _REAL_ANKI_BASE.exists():  # pragma: no cover - platform dependent
    for _candidate in (
        Path.home() / ".local/share/Anki2",  # Linux
        Path.home() / "AppData/Roaming/Anki2",  # Windows
    ):
        if _candidate.exists():
            _REAL_ANKI_BASE = _candidate
            break


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    return {
        str(f.relative_to(root)): (f.stat().st_size, f.stat().st_mtime_ns)
        for f in root.rglob("*")
        if f.is_file()
    }


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    """Every test gets a throwaway HOME, so default cache/log/config paths land in tmp_path."""
    home = tmp_path / "_isolated_home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture(scope="session", autouse=True)
def _real_config_untouched():
    """Fail the session if any test changed a file under the real ~/.config/arete."""
    before = _snapshot(_REAL_ARETE_CONFIG)
    yield
    after = _snapshot(_REAL_ARETE_CONFIG)
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    assert not changed, f"tests touched the real {_REAL_ARETE_CONFIG}: {changed[:10]}"


def _collection_fingerprint(base: Path) -> dict[str, tuple[int, int]]:
    """Size and mtime of every collection.anki2 under an Anki base folder.

    Only the collection files. Media, add-ons and prefs move for reasons that have
    nothing to do with a test run, and watching them gives false failures.
    """
    if not base.exists():
        return {}
    return {
        str(f.relative_to(base)): (f.stat().st_size, f.stat().st_mtime_ns)
        for f in base.glob("*/collection.anki2")
    }


@pytest.fixture(scope="session", autouse=True)
def _real_collection_untouched():
    """Fail the session if any test wrote to a real Anki collection.

    Anki must be closed for this to read cleanly. A running Anki checkpoints its
    write-ahead log on its own schedule, so the mtime moves without any test
    touching it. That case reports a false positive, and the message says so.
    """
    before = _collection_fingerprint(_REAL_ANKI_BASE)
    yield
    after = _collection_fingerprint(_REAL_ANKI_BASE)
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    assert not changed, (
        f"tests touched a real Anki collection under {_REAL_ANKI_BASE}: {changed[:5]}. "
        "If Anki was open during this run, its own checkpoint may explain it. "
        "Close Anki and run again before you trust this failure."
    )


# --- Auto-mark integration tests ---


def pytest_collection_modifyitems(items):
    """Auto-apply the 'integration' marker to all tests under tests/integration/."""
    for item in items:
        if "/integration/" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


# --- Global Config ---


# `anki_url` and `anki_media_dir` live in tests/integration/conftest.py, which is the
# only place that needs them. That version reads the port off a throwaway container on
# a random free port. The copies that used to sit here hardcoded 8766 and a bind-mount
# under docker/anki_data, both left over from the fixed-port compose setup, and both
# shadowed for every test under tests/integration/. Do not add them back here: a
# session-scoped default that points at a real port is exactly how a test run reaches
# a live collection.


@pytest.fixture
def test_deck(anki_url):
    """Creates/Ensures a clean 'IntegrationTest' deck."""
    deck_name = "IntegrationTest"

    # Ensure deck exists and is empty
    requests.post(
        anki_url, json={"action": "createDeck", "version": 6, "params": {"deck": deck_name}}
    )

    # Find notes in deck
    resp = requests.post(
        anki_url,
        json={"action": "findNotes", "version": 6, "params": {"query": f"deck:{deck_name}"}},
    )
    notes = resp.json().get("result", [])
    if notes:
        requests.post(
            anki_url, json={"action": "deleteNotes", "version": 6, "params": {"notes": notes}}
        )

    return deck_name


@pytest.fixture
def setup_anki(anki_url, test_deck):
    """Ensure O2A_Basic model exists with expected fields."""
    # Create it (ignore error if exists)
    requests.post(
        anki_url,
        json={
            "action": "createModel",
            "version": 6,
            "params": {
                "modelName": "O2A_Basic",
                "inOrderFields": ["Front", "Back", "nid"],
                "css": "",
                "cardTemplates": [
                    {
                        "Name": "Card 1",
                        "Front": "{{Front}}",
                        "Back": "{{FrontSide}}\n\n<hr id=answer>\n\n{{Back}}<div style='display:none'>{{nid}}</div>",
                    }
                ],
            },
        },
    )


@pytest.fixture
def run_arete(anki_url):
    """Run the CLI tool in subprocess."""

    def _run(vault_path, anki_url=anki_url, args=None, capture_output=True):
        cmd = [
            sys.executable,
            "-m",
            "arete",
            "-v",
            "sync",
            str(vault_path),
            "--anki-connect-url",
            anki_url,
        ]
        if args:
            cmd.extend(args)

        return subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
        )

    return _run


# --- Mocking Fixtures ---


@pytest.fixture
def mock_home(tmp_path):
    """Mock Path.home() to a temporary directory."""
    with patch("pathlib.Path.home", return_value=tmp_path):
        yield tmp_path


@pytest.fixture
def mock_vault(tmp_path):
    """Create a basic mock vault for unit tests."""
    vault = tmp_path / "MockVault"
    vault.mkdir()
    (vault / "test.md").write_text("# Test Note\n\n- card :: back", encoding="utf-8")
    return vault


@pytest.fixture
def integration_vault(tmp_path):
    """Copy the static integration vault fixtures to a temp dir."""
    src = Path(__file__).parent / "fixtures" / "integration_vault"
    dest = tmp_path / "integration_vault"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest
