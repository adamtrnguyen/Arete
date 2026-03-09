"""Integration test fixtures — manage Anki Docker container lifecycle."""

import json
import os
import shutil
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from arete.application.config import AppConfig
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter


def _find_free_port() -> int:
    """Bind to port 0 to let the OS assign a random free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _anki_is_reachable(url: str, timeout: float = 2.0) -> bool:
    """Probe AnkiConnect's version endpoint with a short timeout."""
    payload = json.dumps({"action": "version", "version": 6}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _wait_for_anki(url: str, max_retries: int = 60, delay: float = 1.0):
    """Poll AnkiConnect until it responds or timeout."""
    for _ in range(max_retries):
        if _anki_is_reachable(url):
            return
        time.sleep(delay)
    raise TimeoutError(f"Anki not reachable at {url} after {max_retries}s")


@pytest.fixture(scope="session")
def _anki_container(tmp_path_factory):
    """Start a clean Anki Docker container on a random port. Tear down after session.

    If ANKI_CONNECT_URL is set, skip Docker management and use the external instance.
    """
    env_url = os.getenv("ANKI_CONNECT_URL")
    if env_url:
        # External Anki provided — caller manages lifecycle
        yield env_url, None
        return

    try:
        import docker as docker_lib
    except ImportError:
        pytest.skip("docker package not installed — run: uv add --dev docker")
        return

    try:
        client = docker_lib.from_env()
        client.ping()
    except docker_lib.errors.DockerException:
        pytest.skip("Docker is not available — start Docker/OrbStack first")
        return

    project_root = Path(__file__).parent.parent.parent
    port = _find_free_port()

    # Create clean anki_data in temp dir (fresh collection every session)
    anki_data = tmp_path_factory.mktemp("anki_data")

    # Seed prefs21.db to skip Anki's first-run wizard
    prefs_dir = anki_data / ".local/share/Anki2"
    prefs_dir.mkdir(parents=True)
    fixture_prefs = project_root / "tests/fixtures/anki/prefs21.db"
    if fixture_prefs.exists():
        shutil.copy(fixture_prefs, prefs_dir / "prefs21.db")

    # Create media directory
    media_dir = anki_data / ".local/share/Anki2/User 1/collection.media"
    media_dir.mkdir(parents=True)

    # Make writable for container uid 1000
    os.chmod(anki_data, 0o777)

    arete_plugin = (project_root / "arete_ankiconnect").resolve()

    container = client.containers.run(
        "ghcr.io/adanato/arete/anki-custom:latest",
        platform="linux/amd64",
        detach=True,
        name=f"arete-integration-{port}",
        environment={
            "PUID": "1000",
            "PGID": "1000",
            "ANKI_LANG": "en",
            "ANKI_VERSION": "24.11",
            "QT_QUICK_BACKEND": "software",
            "ANKI_NOHIGHDPI": "1",
            "ANKICONNECT_BIND_ADDRESS": "0.0.0.0",
        },
        ports={"8765/tcp": ("127.0.0.1", port)},
        volumes={
            str(anki_data.resolve()): {"bind": "/config", "mode": "rw"},
            str(arete_plugin): {
                "bind": "/config/.local/share/Anki2/addons21/arete_ankiconnect",
                "mode": "rw",
            },
        },
        shm_size="1g",
    )

    url = f"http://127.0.0.1:{port}"

    try:
        _wait_for_anki(url)
    except TimeoutError as exc:
        logs = container.logs(tail=50).decode()
        container.stop()
        container.remove()
        raise TimeoutError(
            f"Anki not reachable at {url} after 60s.\nContainer logs:\n{logs}"
        ) from exc

    yield url, anki_data

    container.stop()
    container.remove()


@pytest.fixture(scope="session")
def anki_url(_anki_container):
    """AnkiConnect URL pointing to the Docker-managed Anki instance."""
    return _anki_container[0]


@pytest.fixture(scope="session")
def anki_media_dir(_anki_container):
    """Media directory inside the Docker-managed Anki data."""
    _, anki_data = _anki_container
    if anki_data is None:
        # External Anki — fall back to project docker dir
        p = Path("docker/anki_data/.local/share/Anki2/User 1/collection.media").resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    return anki_data / ".local/share/Anki2/User 1/collection.media"


# ---------------------------------------------------------------------------
# Application-level fixtures (Phase 2)
# ---------------------------------------------------------------------------


@pytest.fixture
def anki_bridge(anki_url) -> AnkiConnectAdapter:
    """Function-scoped AnkiConnectAdapter pointing at Docker Anki."""
    return AnkiConnectAdapter(url=anki_url)


@pytest.fixture
def sync_config(anki_url, anki_media_dir, tmp_path) -> callable:
    """Build an AppConfig via model_construct(), bypassing TOML/env resolution.

    Usage::

        cfg = sync_config(vault_root=some_path)
        cfg = sync_config(vault_root=some_path, dry_run=True)
    """

    def _make(**overrides: Any) -> AppConfig:
        defaults: dict[str, Any] = {
            "anki_connect_url": anki_url,
            "anki_media_dir": anki_media_dir,
            "backend": "ankiconnect",
            "cache_db": str(tmp_path / ".arete_test.db"),
            "clear_cache": True,
            "log_dir": tmp_path / "logs",
            "verbose": 2,
            "keep_going": True,
        }
        defaults.update(overrides)
        # Derive vault_root and root_input from each other when only one is given
        if "vault_root" in defaults and "root_input" not in defaults:
            defaults["root_input"] = defaults["vault_root"]
        if "root_input" in defaults and "vault_root" not in defaults:
            p = Path(defaults["root_input"])
            defaults["vault_root"] = p if p.is_dir() else p.parent

        return AppConfig.model_construct(**defaults)

    return _make


@pytest.fixture
def vault_factory(tmp_path):
    r"""Create a temp vault from an inline ``{filename: content}`` dict.

    Usage::

        vault = vault_factory({"hello.md": "---\narete: true\n...\n"})
    """

    def _make(files: dict[str, str], subdir: str = "vault") -> Path:
        vault = tmp_path / subdir
        vault.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            p = vault / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        return vault

    return _make


@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    """Prevent host config from leaking into tests.

    - Sets HOME to tmp_path so AppConfig won't read ~/.config/arete/config.toml
    - Clears O2A_* env vars that pydantic-settings would pick up
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in list(os.environ):
        if key.startswith("O2A_"):
            monkeypatch.delenv(key)
    # Ensure no stray config file
    (tmp_path / ".config" / "arete").mkdir(parents=True, exist_ok=True)
