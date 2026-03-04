"""Integration test fixtures — skip all tests when Anki is unreachable."""

import urllib.request
import urllib.error
import json

import pytest


def _anki_is_reachable(url: str, timeout: float = 2.0) -> bool:
    """Probe AnkiConnect's version endpoint with a short timeout."""
    payload = json.dumps({"action": "version", "version": 6}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


@pytest.fixture(scope="session", autouse=True)
def _require_anki(anki_url):
    """Skip the entire integration suite when Anki is not reachable."""
    if not _anki_is_reachable(anki_url):
        pytest.skip(
            f"Anki not reachable at {anki_url}. "
            "Run: just docker-up && just wait-for-anki"
        )
