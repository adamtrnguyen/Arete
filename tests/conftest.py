import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

# --- Auto-mark integration tests ---


def pytest_collection_modifyitems(items):
    """Auto-apply the 'integration' marker to all tests under tests/integration/."""
    for item in items:
        if "/integration/" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


# --- Global Config ---


@pytest.fixture(scope="session")
def anki_url():
    """Return the URL for AnkiConnect.

    Default to 8766 (Anki 24+ default/Docker default).
    """
    return os.getenv("ANKI_CONNECT_URL", "http://127.0.0.1:8766")


@pytest.fixture(scope="session")
def anki_media_dir():
    """Return the path to the Docker bind-mount media dir on the host."""
    p = Path("docker/anki_data/.local/share/Anki2/User 1/collection.media").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


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
