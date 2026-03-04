"""Server commands: daemon (HTTP) and MCP (stdio)."""

from typing import Annotated

import typer

serve_app = typer.Typer(name="serve", help="Start Arete servers.")


@serve_app.command("daemon")
def daemon(
    port: Annotated[int, typer.Option(help="Port to bind the server to.")] = 8777,
    host: Annotated[str, typer.Option(help="Host to bind the server to.")] = "127.0.0.1",
    reload: Annotated[bool, typer.Option(help="Enable auto-reload.")] = False,
):
    """Start the persistent background server (HTTP/uvicorn)."""
    import uvicorn

    typer.secho(f"🚀 Starting Arete Server on http://{host}:{port}", fg="green")
    uvicorn.run("arete.interface.http_server:app", host=host, port=port, reload=reload)


@serve_app.command("mcp")
def mcp():
    """Start MCP (Model Context Protocol) server for AI agent integration.

    This exposes Arete's sync capabilities to AI agents like Claude, Gemini, etc.
    Configure in Claude Desktop's config.json:

        {
          "mcpServers": {
            "arete": {
              "command": "arete",
              "args": ["serve", "mcp"]
            }
          }
        }
    """
    from arete.interface.mcp_server import main as mcp_main

    typer.echo("Starting Arete MCP Server...")
    mcp_main()
