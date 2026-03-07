from pathlib import Path
from typing import Protocol

from fastapi.testclient import TestClient

from arete.interface.http_server import app


class AreteRunner(Protocol):
    def sync_vault(
        self,
        vault_path: Path,
        anki_url: str,
        prune: bool = False,
        clear_cache: bool = False,
        force: bool = False,
    ) -> None: ...

    def get_log_output(self) -> str:
        """Return the logs captured from the last run."""
        ...


class CliRunner:
    def __init__(self):
        self._last_result = None

    def sync_vault(
        self,
        vault_path: Path,
        anki_url: str,
        prune: bool = False,
        clear_cache: bool = False,
        force: bool = False,
    ) -> None:
        from typer.testing import CliRunner as TyperRunner

        from arete.interface.cli import app

        cmd = ["-v", "sync", str(vault_path), "--anki-connect-url", anki_url]
        if prune:
            cmd.append("--prune")
        if clear_cache:
            cmd.append("--clear-cache")
        if force:
            cmd.append("--force")

        runner = TyperRunner()
        self._last_result = runner.invoke(app, cmd)
        if self._last_result.exit_code != 0:
            raise RuntimeError(f"CLI failed: {self._last_result.output}")

    def get_log_output(self) -> str:
        if self._last_result:
            return self._last_result.output
        return ""


class ServerRunner:
    def __init__(self):
        self.client = TestClient(app)
        self._last_logs = ""

    def sync_vault(
        self,
        vault_path: Path,
        anki_url: str,
        prune: bool = False,
        clear_cache: bool = False,
        force: bool = False,
    ) -> None:
        payload = {
            "vault_root": str(vault_path),
            "anki_connect_url": anki_url,
            "prune": prune,
            "clear_cache": clear_cache,
            "force": force,
            # Force backend="ankiconnect" to ensure it calls out
            "backend": "ankiconnect",
        }
        resp = self.client.post("/sync", json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"Server failed: {resp.text}")

        # Simulate log output from response for compatibility?
        # Or better: construct a fake log string from stats?
        # "updated/added={total_generated}"
        data = resp.json()
        self._last_logs = f"generated={data['total_generated']} updated/added={data['total_imported']} errors={data['total_errors']}"

    def get_log_output(self) -> str:
        return self._last_logs
