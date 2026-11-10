"""`arete serve`: what each subcommand starts, and with which arguments."""

from unittest.mock import patch

from typer.testing import CliRunner

from arete.interface.cli import app

runner = CliRunner()


def test_serve_daemon_defaults_bind_loopback_without_reload():
    with patch("uvicorn.run") as run:
        result = runner.invoke(app, ["serve", "daemon"])
    assert result.exit_code == 0
    assert "http://127.0.0.1:8777" in result.stdout
    run.assert_called_once_with(
        "arete.interface.http_server:app", host="127.0.0.1", port=8777, reload=False
    )


def test_serve_daemon_passes_host_and_reload_through():
    with patch("uvicorn.run") as run:
        result = runner.invoke(app, ["serve", "daemon", "--host", "0.0.0.0", "--reload"])
    assert result.exit_code == 0
    run.assert_called_once_with(
        "arete.interface.http_server:app", host="0.0.0.0", port=8777, reload=True
    )


def test_serve_mcp_announces_then_starts_the_stdio_server():
    with patch("arete.interface.mcp_server.main") as main:
        result = runner.invoke(app, ["serve", "mcp"])
    assert result.exit_code == 0
    assert "Starting Arete MCP Server..." in result.stdout
    main.assert_called_once_with()
