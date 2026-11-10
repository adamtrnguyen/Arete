import errno
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import requests
from hypothesis import settings as hypothesis_settings

# --- Hypothesis: no wall-clock deadline ---
# A deadline fails a correct property when the machine is busy (pytest-xdist workers, the
# twelve concurrent `just qa` checks). Correctness is what a property asserts; the number of
# examples bounds the run time. A test that needs a deadline sets its own with @settings.

hypothesis_settings.register_profile("arete", deadline=None)
hypothesis_settings.load_profile("arete")

# --- Isolation: no test may touch the user's real ~/.config/arete ---
# arete's defaults (cache.db, logs/, config.toml) all hang off Path.home(), so a test that
# escapes the redirect writes into the real ones (docs/history/test-isolation.md).

_REAL_ARETE_CONFIG = Path.home() / ".config" / "arete"

# --- Isolation: no test may touch the user's real Anki collection ---
# detect_anki_paths() in application/utils/common.py builds the collection path from
# Path.home(), so the _isolated_home fixture below already redirects it. This is the
# belt to that fixture's braces: if a test ever resolves the real path anyway, the
# session fails loudly instead of writing notes into a live deck
# (docs/history/test-isolation.md).

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


# --- Isolation: no test may reach a running Anki over AnkiConnect ---
# The HOME redirect above does nothing for the network. AppConfig defaults to
# http://127.0.0.1:8765 and backend "auto", and the factory picks AnkiConnect when that
# port answers. With Anki open, any test that builds a default config and reaches a
# bridge would sync into the live collection. Refuse the connection at the socket, so
# the adapter sees "Anki not running" and auto-selection falls back to the direct
# backend under the isolated HOME. respx mocks never open a socket, so they still work.
# The integration container listens on a random port, so it is not affected.

_ANKICONNECT_PORT = 8765
_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
blocked_ankiconnect_attempts: list[object] = []


def _is_ankiconnect(address: object) -> bool:
    return isinstance(address, tuple) and len(address) >= 2 and address[1] == _ANKICONNECT_PORT


def _guarded_connect(self, address):
    if _is_ankiconnect(address):
        blocked_ankiconnect_attempts.append(os.environ.get("PYTEST_CURRENT_TEST", address))
        raise ConnectionRefusedError(f"tests may not reach AnkiConnect at {address}")
    return _REAL_CONNECT(self, address)


def _guarded_connect_ex(self, address):
    if _is_ankiconnect(address):
        blocked_ankiconnect_attempts.append(os.environ.get("PYTEST_CURRENT_TEST", address))
        return errno.ECONNREFUSED
    return _REAL_CONNECT_EX(self, address)


@pytest.fixture(autouse=True)
def _no_live_ankiconnect(monkeypatch):
    """Refuse every socket connection to port 8765, and drop ARETE_* from the environment."""
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
    for key in [k for k in os.environ if k.startswith("ARETE_")]:
        monkeypatch.delenv(key)


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    """Every test gets a throwaway HOME, so default cache/log/config paths land in tmp_path."""
    home = tmp_path / "_isolated_home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def configured_vault(monkeypatch, tmp_path):
    """A vault configured through ARETE_VAULT_ROOT. Its name has a space, as the real one."""
    vault = tmp_path / "Obsidian Vault"
    vault.mkdir()
    monkeypatch.setenv("ARETE_VAULT_ROOT", str(vault))
    return vault.resolve()


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
# only place that needs them; it reads the port off a throwaway container on a random
# free port. Do not define them here: a session-scoped default that points at a real
# port is how a test run reaches a live collection.


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
    """A clean test deck. The Basic and Cloze note types ship with Anki."""
    return test_deck


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


def pytest_terminal_summary(terminalreporter):
    """Say how many connections to a live AnkiConnect the guard refused this run."""
    if blocked_ankiconnect_attempts:
        terminalreporter.write_line(
            f"AnkiConnect guard: refused {len(blocked_ankiconnect_attempts)} connection(s) "
            "to port 8765 (no test reached a running Anki)."
        )
        for who in sorted({str(a) for a in blocked_ankiconnect_attempts}):
            terminalreporter.write_line(f"  {who}")
