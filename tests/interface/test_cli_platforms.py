"""Tests for cross-platform behavior of config open and logs commands."""

from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

from typer.testing import CliRunner

from arete.interface.cli import app

runner = CliRunner()


# --- Config Open ---


def test_config_open_create_if_missing():
    with (
        patch("pathlib.Path.exists", return_value=False),
        patch("pathlib.Path.mkdir") as mock_mkdir,
        patch("pathlib.Path.touch") as mock_touch,
        patch("subprocess.run"),
        patch("sys.platform", "darwin"),
    ):
        result = runner.invoke(app, ["config", "open"])
        assert result.exit_code == 0
        mock_mkdir.assert_called()
        mock_touch.assert_called()


def test_config_open_win32():
    with (
        patch("pathlib.Path.exists", return_value=True),
        patch("os.startfile", create=True) as mock_start,
        patch("sys.platform", "win32"),
    ):
        result = runner.invoke(app, ["config", "open"])
        assert result.exit_code == 0
        mock_start.assert_called()


def test_config_open_linux():
    with (
        patch("pathlib.Path.exists", return_value=True),
        patch("subprocess.run") as mock_run,
        patch("sys.platform", "linux"),
    ):
        result = runner.invoke(app, ["config", "open"])
        assert result.exit_code == 0
        mock_run.assert_called_with(["xdg-open", ANY])


# --- Logs ---


def test_logs_darwin():
    with patch("arete.interface.cli.resolve_config") as mock_conf:
        mock_config = MagicMock()
        mock_config.log_dir = Path("/tmp/logs")
        mock_conf.return_value = mock_config

        with patch("sys.platform", "darwin"), patch("subprocess.run") as mock_sub:
            result = runner.invoke(app, ["logs"])
            assert result.exit_code == 0
            mock_sub.assert_called_once()
            call_args = mock_sub.call_args[0][0]
            assert call_args[0] == "open"
            assert str(call_args[1]) == str(Path("/tmp/logs"))


def test_logs_mkdir_and_open_win32():
    with patch("arete.interface.cli.resolve_config") as mock_conf:
        mock_conf.return_value.log_dir.exists.return_value = False

        with patch("sys.platform", "win32"), patch("os.startfile", create=True) as mock_start:
            result = runner.invoke(app, ["logs"])
            assert result.exit_code == 0
            mock_conf.return_value.log_dir.mkdir.assert_called()
            mock_start.assert_called()


def test_logs_linux():
    with patch("arete.interface.cli.resolve_config") as mock_conf:
        mock_conf.return_value.log_dir.exists.return_value = True

        with patch("sys.platform", "linux"), patch("subprocess.run") as mock_run:
            result = runner.invoke(app, ["logs"])
            assert result.exit_code == 0
            mock_run.assert_called()


def test_logs_missing_opener_exits_1_cleanly():
    with patch("arete.interface.cli.resolve_config") as mock_conf:
        mock_conf.return_value.log_dir = Path("/tmp/logs")
        with (
            patch("sys.platform", "linux"),
            patch("subprocess.run", side_effect=FileNotFoundError(2, "No such file", "xdg-open")),
        ):
            result = runner.invoke(app, ["logs"])
            assert result.exit_code == 1
            assert "xdg-open not found" in result.stderr
