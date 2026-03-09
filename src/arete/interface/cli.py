"""Arete CLI — root commands and subgroup registration."""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Annotated, Literal

import typer

from arete.application.config import resolve_config
from arete.interface._common import _resolve_with_overrides
from arete.interface.anki_commands import anki_app
from arete.interface.serve_commands import serve_app
from arete.interface.vault_commands import vault_app

# ---------------------------------------------------------------------------
# Root app
# ---------------------------------------------------------------------------

app = typer.Typer(
    help="arete: Pro-grade Obsidian to Anki sync tool.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

app.add_typer(vault_app, name="vault")
app.add_typer(anki_app, name="anki")
app.add_typer(serve_app, name="serve")

config_app = typer.Typer(help="Manage arete configuration.")
app.add_typer(config_app, name="config")

graph_app = typer.Typer(help="Dependency graph diagnostics.", no_args_is_help=True)
app.add_typer(graph_app, name="graph")


# ---------------------------------------------------------------------------
# Global callback
# ---------------------------------------------------------------------------


@app.callback()
def main_callback(
    ctx: typer.Context,
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose", "-v", count=True, help="Increase verbosity. Repeat for more detail."
        ),
    ] = 1,
):
    """Global settings for arete."""
    ctx.ensure_object(dict)
    ctx.obj["verbose_bonus"] = verbose


# ---------------------------------------------------------------------------
# Root commands
# ---------------------------------------------------------------------------


@app.command()
def sync(
    ctx: typer.Context,
    path: Annotated[
        Path | None,
        typer.Argument(
            help=(
                "Path to Obsidian vault or Markdown file. "
                "Defaults to 'vault_root' in config, or CWD."
            )
        ),
    ] = None,
    backend: Annotated[
        str | None, typer.Option(help="Anki backend: auto, ankiconnect, direct.")
    ] = None,
    prune: Annotated[
        bool, typer.Option("--prune/--no-prune", help="Prune orphaned cards from Anki.")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Bypass confirmation for destructive actions.")
    ] = False,
    clear_cache: Annotated[
        bool, typer.Option("--clear-cache", help="Force re-sync of all files.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Verify changes without applying.")
    ] = False,
    anki_connect_url: Annotated[
        str | None, typer.Option(help="Custom AnkiConnect endpoint.")
    ] = None,
    anki_media_dir: Annotated[
        Path | None, typer.Option(help="Custom Anki media directory.")
    ] = None,
    workers: Annotated[int | None, typer.Option(help="Parallel sync workers.")] = None,
):
    """[bold green]Sync[/bold green] your Obsidian notes to Anki."""
    config = _resolve_with_overrides(
        root_input=path,
        backend=backend,
        prune=prune,
        force=force,
        clear_cache=clear_cache,
        dry_run=dry_run,
        anki_connect_url=anki_connect_url,
        anki_media_dir=anki_media_dir,
        workers=workers,
        verbose=ctx.obj.get("verbose_bonus", 1),
    )

    import asyncio

    from arete.application.orchestrator import SyncFailedError, run_sync_logic

    try:
        asyncio.run(run_sync_logic(config))
    except SyncFailedError as e:
        raise typer.Exit(1) from e


@app.command()
def init():
    """Launch the interactive setup wizard."""
    from arete.application.wizard import run_init_wizard

    run_init_wizard()
    raise typer.Exit()


@app.command()
def logs():
    """Open the log directory."""
    import subprocess

    config = resolve_config()
    if not config.log_dir.exists():
        config.log_dir.mkdir(parents=True, exist_ok=True)

    if sys.platform == "darwin":
        subprocess.run(["open", str(config.log_dir)])
    elif sys.platform == "win32":
        os.startfile(str(config.log_dir))
    else:
        subprocess.run(["xdg-open", str(config.log_dir)])


@app.command("queue")
def queue(
    ctx: typer.Context,
    path: Annotated[
        Path | None, typer.Argument(help="Path to Obsidian vault. Defaults to config.")
    ] = None,
    deck: Annotated[str | None, typer.Option(help="Filter by deck name.")] = None,
    depth: Annotated[int, typer.Option(help="Prerequisite search depth.")] = 2,
    include_new: Annotated[
        bool, typer.Option("--include-new", help="Include new (unreviewed) cards.")
    ] = False,
    include_related: Annotated[
        bool,
        typer.Option(
            "--include-related",
            help="Reserved for future related-card boost. Currently not implemented.",
        ),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show plan without creating decks.")
    ] = False,
    cross_deck: Annotated[
        bool,
        typer.Option(
            "--cross-deck",
            help="Pull in prerequisites from other decks. Without this, --deck is an isolated set.",
        ),
    ] = False,
    algo: Annotated[
        Literal["static", "dynamic"],
        typer.Option(
            "--algo",
            help=(
                "Queue algorithm. "
                "'static' = build once and study as-is. "
                "'dynamic' = ready-frontier ordering for prerequisite-aware sequencing."
            ),
        ),
    ] = "static",
):
    """Build a dependency-sorted filtered deck in Anki.

    Uses due cards by default (optionally includes new cards), resolves
    prerequisites up to --depth, and writes the ordered result to
    the filtered deck 'Arete::Queue'.

    By default, discovered prerequisites are included (no user-facing
    weakness thresholds yet).

    When --deck is specified without --cross-deck, only cards in that deck
    are included (isolated set). Use --cross-deck to also pull in
    prerequisites from other decks.
    """
    import asyncio

    from arete.application.factory import get_anki_bridge
    from arete.application.queue.service import build_study_queue

    config = _resolve_with_overrides(root_input=path)
    vault_root = config.root_input
    if vault_root is None:
        typer.secho("No vault root configured. Pass a path or set O2A_ROOT_INPUT.", fg="red")
        raise typer.Exit(1)

    if include_related:
        typer.secho(
            "--include-related is not implemented yet. Re-run without this flag.",
            fg="yellow",
        )
        raise typer.Exit(2)

    async def run():
        anki = await get_anki_bridge(config)
        try:
            result = await build_study_queue(
                anki,
                vault_root,
                deck=deck,
                depth=depth,
                include_new=include_new,
                include_related=include_related,
                cross_deck=cross_deck,
                algo=algo,
                dry_run=dry_run,
                enrich=False,
            )

            if result.due_count == 0:
                typer.secho("No cards found.", fg="yellow")
                return

            if result.unmapped_count > 0:
                typer.secho(
                    f"WARNING: {result.unmapped_count}/{result.due_count}"
                    " cards have no Arete ID tag."
                    " Run 'arete sync' to assign IDs and fix this.",
                    fg="yellow",
                )

            typer.echo(f"Due cards: {result.due_count} ({result.unmapped_count} without Arete IDs)")
            typer.echo(f"Algorithm: {algo}")
            if result.prereq_count:
                typer.echo(f"Weak prerequisites: {result.prereq_count}")
            typer.echo(f"Main queue: {result.main_count}")
            if result.missing_prereqs:
                typer.secho(f"Missing prereqs: {result.missing_prereqs}", fg="yellow")
            if result.cycles:
                typer.secho(f"Cycles detected: {len(result.cycles)}", fg="yellow")

            if not dry_run and result.deck_created:
                typer.secho(
                    f"Created '{result.deck_name}' with {result.deck_card_count} cards.",
                    fg="green",
                )
            elif not dry_run and result.total_queued > 0 and not result.deck_created:
                typer.secho("Failed to create filtered deck.", fg="red")
        finally:
            await anki.close()

    asyncio.run(run())


@app.command()
def report(
    json_output: Annotated[
        bool, typer.Option("--json", help="Output as JSON for programmatic use.")
    ] = False,
    clear: Annotated[
        int | None,
        typer.Option(
            "--clear",
            help="Clear reports. Pass 0 to clear all, or a 1-based index to clear one.",
        ),
    ] = None,
):
    """Show or clear reported card issues from Anki review sessions."""
    from arete.application.report_service import clear_reports, load_reports

    if clear is not None:
        if clear == 0:
            cleared = clear_reports()
        else:
            cleared = clear_reports([clear])

        if not cleared:
            typer.secho("No reports to clear.", fg="yellow")
            return

        # Reconcile: unsuspend cards no longer in the report list
        remaining = load_reports()
        remaining_cids = {r["cid"] for r in remaining if r.get("cid")}
        to_unsuspend = [
            r["cid"] for r in cleared if r.get("cid") and r["cid"] not in remaining_cids
        ]

        if to_unsuspend:
            import asyncio

            from arete.application.factory import get_anki_bridge

            config = _resolve_with_overrides()

            async def unsuspend():
                anki = await get_anki_bridge(config)
                try:
                    return await anki.unsuspend_cards(to_unsuspend)
                finally:
                    await anki.close()

            try:
                asyncio.run(unsuspend())
                typer.secho(
                    f"Cleared {len(cleared)} report(s), unsuspended {len(to_unsuspend)} card(s).",
                    fg="green",
                )
            except Exception:
                typer.secho(
                    f"Cleared {len(cleared)} report(s). Could not connect to Anki to unsuspend.",
                    fg="yellow",
                )
        else:
            typer.secho(f"Cleared {len(cleared)} report(s).", fg="green")
        return

    reports = load_reports()
    if not reports:
        typer.secho("No reported cards.", fg="green")
        return

    if json_output:
        typer.echo(json.dumps(reports, indent=2, ensure_ascii=False))
        return

    typer.echo(f"Reported cards: {len(reports)}\n")
    for i, r in enumerate(reports, 1):
        file_path = r.get("file_path", "?")
        line = r.get("line", "?")
        arete_id = r.get("arete_id", "")
        front = r.get("front", "")
        note = r.get("note", "")
        ts = r.get("timestamp", "")

        # Format timestamp: strip timezone offset for display
        when = ts[:16].replace("T", " ") if ts else "?"

        id_part = f" ({arete_id})" if arete_id else ""
        typer.echo(f"{i}. {file_path}:{line}{id_part}")
        typer.echo(f"   Front: {front}")
        typer.echo(f"   Issue: {note}")
        typer.echo(f"   When:  {when}")
        typer.echo()


# ---------------------------------------------------------------------------
# Config subgroup
# ---------------------------------------------------------------------------


@config_app.command("show")
def config_show():
    """Display final resolved configuration."""
    config = resolve_config()
    d = {k: str(v) if isinstance(v, Path) else v for k, v in config.model_dump().items()}
    typer.echo(json.dumps(d, indent=2))


@config_app.command("open")
def config_open():
    """Open the config file in your default editor."""
    import subprocess

    cfg_path = Path.home() / ".config/arete/config.toml"
    if not cfg_path.exists():
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.touch()

    if sys.platform == "darwin":
        subprocess.run(["open", str(cfg_path)])
    elif sys.platform == "win32":
        os.startfile(str(cfg_path))
    else:
        subprocess.run(["xdg-open", str(cfg_path)])


# ---------------------------------------------------------------------------
# Graph subgroup
# ---------------------------------------------------------------------------


@graph_app.command("check")
def graph_check(
    path: Annotated[Path | None, typer.Argument(help="Vault path override.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
):
    """Check dependency graph health: cycles, isolated cards, missing refs, components."""
    from dataclasses import asdict

    from arete.application.queue.graph_resolver import check_graph_health

    config = _resolve_with_overrides(root_input=path)
    vault_root = config.root_input
    if vault_root is None:
        typer.secho("No vault root configured. Pass a path or set O2A_ROOT_INPUT.", fg="red")
        raise typer.Exit(1)

    result = check_graph_health(vault_root)

    if json_output:
        typer.echo(json.dumps(asdict(result), indent=2))
    else:
        typer.echo(
            f"Nodes: {result.total_nodes}  Edges: {result.total_edges}"
            f"  Components: {result.components}  Roots: {result.roots}"
        )

        if result.cycles:
            typer.secho(f"\nCycles: {len(result.cycles)}", fg="red")
            for cycle in result.cycles:
                titles = [entry.title for entry in cycle]
                typer.echo(f"  {' -> '.join(titles)}")
        else:
            typer.secho("Cycles: 0", fg="green")

        if result.isolated_nodes:
            typer.secho(
                f"\nIsolated cards (no deps in or out): {len(result.isolated_nodes)}",
                fg="yellow",
            )
            for entry in result.isolated_nodes:
                typer.echo(f"  {entry.title}  ({Path(entry.file).name})")
        else:
            typer.secho("Isolated: 0", fg="green")

        if result.unresolved_refs:
            total_refs = sum(len(e.missing_refs) for e in result.unresolved_refs)
            typer.secho(f"\nUnresolved references: {total_refs}", fg="red")
            for entry in result.unresolved_refs:
                for ref in entry.missing_refs:
                    typer.echo(f"  {entry.title} -> {ref}  (missing)")
        else:
            typer.secho("Unresolved: 0", fg="green")

        if result.components > 1:
            typer.secho(f"\nConnected components: {result.components}", fg="yellow")

        if result.cycles or result.unresolved_refs:
            raise typer.Exit(1)
