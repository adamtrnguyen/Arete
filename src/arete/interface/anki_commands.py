"""Anki card management and debugging commands."""

import asyncio
import json
from typing import Annotated

import typer

from arete.interface._common import _resolve_with_overrides

anki_app = typer.Typer(name="anki", help="Anki card management and debugging.")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_cids(cids: str) -> list[int]:
    """Parse comma-separated or JSON list of card IDs."""
    if cids.startswith("["):
        return json.loads(cids)
    return [int(n.strip()) for n in cids.split(",") if n.strip().isdigit()]


def _run_anki_bridge_action(
    action_fn, *, result_key: str | None = "ok", **config_kwargs
) -> None:
    """Run an async AnkiBridge action with standard config/bridge setup.

    If *result_key* is given, the output is ``{result_key: value}``; when
    ``None``, the raw value is printed as JSON.
    """
    from arete.application.factory import get_anki_bridge

    async def _run():
        config = _resolve_with_overrides(**config_kwargs)
        anki = await get_anki_bridge(config)
        result = await action_fn(anki)
        if result_key is not None:
            print(json.dumps({result_key: result}))
        else:
            print(json.dumps(result))

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@anki_app.command("stats")
def anki_stats(
    ctx: typer.Context,
    nids: Annotated[str, typer.Option(help="Comma-separated list of Note IDs (or JSON list).")],
    json_output: Annotated[
        bool, typer.Option("--json/--no-json", help="Output results as JSON.")
    ] = True,
    backend: Annotated[
        str | None, typer.Option(help="Force backend (auto|apy|ankiconnect)")
    ] = None,
    anki_connect_url: Annotated[str | None, typer.Option(help="AnkiConnect URL Override")] = None,
    anki_base: Annotated[str | None, typer.Option(help="Anki Base Directory Override")] = None,
):
    """Fetch card statistics for the given Note IDs."""
    from dataclasses import asdict

    # Parse NIDs
    nids_list = []
    if nids.startswith("["):
        try:
            nids_list = json.loads(nids)
        except json.JSONDecodeError as e:
            typer.secho("Invalid JSON for --nids", fg="red")
            raise typer.Exit(1) from e
    else:
        nids_list = [int(n.strip()) for n in nids.split(",") if n.strip().isdigit()]

    if not nids_list:
        if json_output:
            typer.echo("[]")
        else:
            typer.echo("No valid NIDs provided.")
        return

    async def run():
        verbose = 1
        if ctx.parent and ctx.parent.obj:
            verbose = ctx.parent.obj.get("verbose_bonus", 1)

        config = _resolve_with_overrides(
            verbose=verbose,
            backend=backend,
            anki_connect_url=anki_connect_url,
            anki_base=anki_base,
        )

        from arete.application.factory import get_stats_repo
        from arete.application.stats.metrics_calculator import MetricsCalculator
        from arete.application.stats.service import FsrsStatsService

        repo = get_stats_repo(config)
        service = FsrsStatsService(repo=repo, calculator=MetricsCalculator())
        return await service.get_enriched_stats(nids_list)

    stats = asyncio.run(run())
    result = [asdict(s) for s in stats]

    if json_output:
        typer.echo(json.dumps(result, indent=2))
    else:
        import rich
        from rich.table import Table

        t = Table(title="Card Stats")
        t.add_column("CID")
        t.add_column("Deck")
        t.add_column("Diff")
        for s in result:
            diff_str = f"{int(s['difficulty'] * 100)}%" if s["difficulty"] is not None else "-"
            t.add_row(str(s["card_id"]), s["deck_name"], diff_str)
        rich.print(t)


@anki_app.command("suspend")
def suspend_cards(
    ctx: typer.Context,
    cids: Annotated[str, typer.Option(help="Comma-separated list of Card IDs (or JSON list).")],
    backend: Annotated[str | None, typer.Option(help="Force backend")] = None,
    anki_connect_url: Annotated[str | None, typer.Option(help="AnkiConnect URL Override")] = None,
    anki_base: Annotated[str | None, typer.Option(help="Anki Base Directory Override")] = None,
):
    """Suspend cards by CID."""
    cids_list = _parse_cids(cids)
    _run_anki_bridge_action(
        lambda anki: anki.suspend_cards(cids_list),
        backend=backend,
        anki_connect_url=anki_connect_url,
        anki_base=anki_base,
    )


@anki_app.command("unsuspend")
def unsuspend_cards(
    ctx: typer.Context,
    cids: Annotated[str, typer.Option(help="Comma-separated list of Card IDs.")],
    backend: Annotated[str | None, typer.Option(help="Force backend")] = None,
    anki_connect_url: Annotated[str | None, typer.Option(help="AnkiConnect URL Override")] = None,
    anki_base: Annotated[str | None, typer.Option(help="Anki Base Directory Override")] = None,
):
    """Unsuspend cards by CID."""
    cids_list = _parse_cids(cids)
    _run_anki_bridge_action(
        lambda anki: anki.unsuspend_cards(cids_list),
        backend=backend,
        anki_connect_url=anki_connect_url,
        anki_base=anki_base,
    )


@anki_app.command("model-css")
def model_css(
    ctx: typer.Context,
    model: str = typer.Argument(..., help="Model Name"),
    backend: Annotated[str | None, typer.Option(help="Force backend")] = None,
    anki_connect_url: Annotated[str | None, typer.Option(help="AnkiConnect URL Override")] = None,
    anki_base: Annotated[str | None, typer.Option(help="Anki Base Directory Override")] = None,
):
    """Get CSS styling for a model."""
    _run_anki_bridge_action(
        lambda anki: anki.get_model_styling(model),
        result_key="css",
        backend=backend,
        anki_connect_url=anki_connect_url,
        anki_base=anki_base,
    )


@anki_app.command("model-templates")
def model_templates(
    ctx: typer.Context,
    model: str = typer.Argument(..., help="Model Name"),
    backend: Annotated[str | None, typer.Option(help="Force backend")] = None,
    anki_connect_url: Annotated[str | None, typer.Option(help="AnkiConnect URL Override")] = None,
    anki_base: Annotated[str | None, typer.Option(help="Anki Base Directory Override")] = None,
):
    """Get templates for a model."""
    _run_anki_bridge_action(
        lambda anki: anki.get_model_templates(model),
        result_key=None,
        backend=backend,
        anki_connect_url=anki_connect_url,
        anki_base=anki_base,
    )


@anki_app.command("browse")
def anki_browse(
    ctx: typer.Context,
    query: Annotated[str | None, typer.Option(help="Search query (e.g. 'nid:123')")] = None,
    nid: Annotated[int | None, typer.Option(help="Jump to Note ID")] = None,
    backend: Annotated[str | None, typer.Option(help="Force backend")] = None,
    anki_connect_url: Annotated[str | None, typer.Option(help="AnkiConnect URL Override")] = None,
    anki_base: Annotated[str | None, typer.Option(help="Anki Base Directory Override")] = None,
):
    """Open Anki browser."""
    if not query and not nid:
        typer.secho("Must specify --query or --nid", fg="red")
        raise typer.Exit(1)

    final_query = query or f"nid:{nid}"
    _run_anki_bridge_action(
        lambda anki: anki.gui_browse(final_query),
        backend=backend,
        anki_connect_url=anki_connect_url,
        anki_base=anki_base,
    )
