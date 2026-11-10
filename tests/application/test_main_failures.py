from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from arete.application.config import AppConfig
from arete.application.sync.pipeline import RunStats
from arete.composition.orchestrator import SyncFailedError, run_sync_logic
from arete.domain.models import AddonOutdated


@pytest.mark.asyncio
async def test_run_sync_logic_failure_exit():
    """Test that run_sync_logic raises SyncFailedError if stats.total_errors > 0."""
    # root_input must be set to pass assertion in main.py
    # We use valid Path objects
    config = AppConfig(
        root_input=Path("/tmp"),
        vault_root=Path("/tmp"),
        keep_going=False,
        anki_media_dir=Path("/tmp/m"),
    )

    # We must also ensure resolve_path logic inside config isn't stripping it if it doesn't exist?
    # AppConfig logic usually relies on checks.
    # Let's force set the attributes directly if needed, but constructor dict is safer if Config handles it.
    config.root_input = Path("/tmp")
    config.vault_root = Path("/tmp")
    config.anki_media_dir = Path("/tmp/m")

    # Mock return stats with error
    mock_stats = RunStats(total_generated=0, total_imported=0, total_errors=5)

    with patch("arete.composition.orchestrator.run_pipeline", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = mock_stats

        # We also need to mock AnkiConnectAdapter / AnkiApyAdapter construction to avoid network calls
        with patch("arete.composition.factory.AnkiConnectAdapter") as mock_adapter:
            mock_instance = mock_adapter.return_value
            mock_instance.is_responsive = AsyncMock(return_value=True)
            mock_instance.close = AsyncMock()

            with pytest.raises(SyncFailedError):
                await run_sync_logic(config)


@pytest.mark.asyncio
async def test_an_outdated_addon_stops_the_sync_with_its_message(tmp_path: Path, caplog):
    config = AppConfig(
        root_input=tmp_path, vault_root=tmp_path, anki_media_dir=tmp_path / "m", keep_going=True
    )
    outdated = AddonOutdated("Update the arete_ankiconnect add-on and restart Anki.")

    with patch("arete.composition.orchestrator.run_pipeline", AsyncMock(side_effect=outdated)):
        with patch("arete.composition.factory.AnkiConnectAdapter") as mock_adapter:
            mock_adapter.return_value.is_responsive = AsyncMock(return_value=True)
            mock_adapter.return_value.close = AsyncMock()
            with pytest.raises(SyncFailedError, match="restart Anki"):
                await run_sync_logic(config)

    assert any("restart Anki" in r.getMessage() for r in caplog.records)


def test_main_module_invoke():
    """Test calling arete.__main__ logic via runpy to cover the __name__ == "__main__" block."""
    import runpy

    with patch("arete.interface.cli.app") as mock_app:
        # executing the module as "__main__"
        runpy.run_module("arete.__main__", run_name="__main__")
        mock_app.assert_called_once()
