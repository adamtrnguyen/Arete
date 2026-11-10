from pathlib import Path
from typing import Protocol


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
        """Initialize CliRunner."""
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
            cmd.extend(["--prune", "--yes"])
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
