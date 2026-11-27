"""Anki card management and debugging commands."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import typer

from arete.interface._common import (
    _resolve_with_overrides,
    get_verbose_override,
    stdout_to_stderr,
)

anki_app = typer.Typer(
    name="anki",
    help="Anki card management and debugging.",
    no_args_is_help=True,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json_int_list(raw: str, flag: str) -> list[int]:
    """Parse a JSON array of integers, exiting 1 with a clear error otherwise."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        typer.secho(f"Invalid JSON for {flag}", fg="red", err=True)
        raise typer.Exit(1) from e
    if not isinstance(parsed, list):
        typer.secho(f"Invalid {flag}: expected a JSON list of integers", fg="red", err=True)
        raise typer.Exit(1)
    out: list[int] = []
    for item in parsed:
        if isinstance(item, bool) or not isinstance(item, (int, str)):
            typer.secho(f"Invalid {flag}: all items must be integers", fg="red", err=True)
            raise typer.Exit(1)
        try:
            out.append(int(item))
        except ValueError as e:
            typer.secho(f"Invalid {flag}: all items must be integers", fg="red", err=True)
            raise typer.Exit(1) from e
    return out


def _parse_int_list(raw: str, flag: str) -> list[int]:
    """Parse comma-separated or JSON list of integer IDs."""
    stripped = raw.strip()
    if not stripped:
        return []
    if stripped.startswith(("[", "{")):
        return _parse_json_int_list(stripped, flag)

    out: list[int] = []
    for part in stripped.split(","):
        token = part.strip()
        if not token:
            continue
        if not token.isdigit():
            typer.secho(
                f"Invalid {flag}: expected integers (got {token!r})",
                fg="red",
                err=True,
            )
            raise typer.Exit(1)
        out.append(int(token))
    return out


def _parse_ids(ids: str) -> list[str]:
    """Parse a JSON list or a comma-separated list of Arete ids."""
    stripped = ids.strip()
    if not stripped:
        return []
    if stripped.startswith(("[", "{")):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as e:
            typer.secho("Invalid JSON for --ids", fg="red", err=True)
            raise typer.Exit(1) from e
        if not isinstance(parsed, list):
            typer.secho("Invalid --ids: expected a JSON list of strings", fg="red", err=True)
            raise typer.Exit(1)
        out: list[str] = []
        for item in parsed:
            if not isinstance(item, str):
                typer.secho("Invalid --ids: all items must be strings", fg="red", err=True)
                raise typer.Exit(1)
            out.append(item)
        return out
    return [i.strip() for i in stripped.split(",") if i.strip()]


def _run_or_exit(coro):
    """Run the coroutine; a failure prints its reason on stderr and exits 1.

    Stdout stays clean for the JSON a command prints after.
    """
    try:
        with stdout_to_stderr():
            return asyncio.run(coro)
    except Exception as e:
        typer.secho(f"Error: {e}", fg="red", err=True)
        raise typer.Exit(1) from e


def _run_anki_action(
    action_fn: Callable[[Any], Awaitable[Any]],
    *,
    result_key: str | None = "ok",
    **config_kwargs,
) -> None:
    """Run an async bridge action with standard config/bridge setup and JSON output.

    If *result_key* is given, the output is ``{result_key: value}``; when
    ``None``, the raw value is printed as JSON.
    """
    from arete.composition.factory import get_anki_bridge

    async def _run():
        config = _resolve_with_overrides(**config_kwargs)
        anki = await get_anki_bridge(config)
        try:
            return await action_fn(anki)
        finally:
            await anki.close()

    result = _run_or_exit(_run())
    typer.echo(json.dumps(result if result_key is None else {result_key: result}))


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
        str | None, typer.Option(help="Force backend (auto|ankiconnect|direct)")
    ] = None,
    anki_connect_url: Annotated[str | None, typer.Option(help="AnkiConnect URL Override")] = None,
    anki_base: Annotated[str | None, typer.Option(help="Anki Base Directory Override")] = None,
):
    """Fetch card statistics for the given Note IDs."""
    from dataclasses import asdict

    nids_list = _parse_int_list(nids, "--nids")

    if not nids_list:
        if json_output:
            typer.echo("[]")
        else:
            typer.echo("No valid NIDs provided.")
        return

    async def run():
        config = _resolve_with_overrides(
            verbose=get_verbose_override(ctx),
            backend=backend,
            anki_connect_url=anki_connect_url,
            anki_base=anki_base,
        )

        from arete.composition.factory import get_stats_service

        service = await get_stats_service(config)
        try:
            return await service.get_enriched_stats(nids_list)
        finally:
            await service.close()

    stats = _run_or_exit(run())
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
    cids: Annotated[str, typer.Option(help="Comma-separated list of Card IDs (or JSON list).")],
    backend: Annotated[str | None, typer.Option(help="Force backend")] = None,
    anki_connect_url: Annotated[str | None, typer.Option(help="AnkiConnect URL Override")] = None,
    anki_base: Annotated[str | None, typer.Option(help="Anki Base Directory Override")] = None,
):
    """Suspend cards by CID."""
    cids_list = _parse_int_list(cids, "--cids")
    _run_anki_action(
        lambda anki: anki.suspend_cards(cids_list),
        backend=backend,
        anki_connect_url=anki_connect_url,
        anki_base=anki_base,
    )


@anki_app.command("unsuspend")
def unsuspend_cards(
    cids: Annotated[str, typer.Option(help="Comma-separated list of Card IDs (or JSON list).")],
    backend: Annotated[str | None, typer.Option(help="Force backend")] = None,
    anki_connect_url: Annotated[str | None, typer.Option(help="AnkiConnect URL Override")] = None,
    anki_base: Annotated[str | None, typer.Option(help="Anki Base Directory Override")] = None,
):
    """Unsuspend cards by CID."""
    cids_list = _parse_int_list(cids, "--cids")
    _run_anki_action(
        lambda anki: anki.unsuspend_cards(cids_list),
        backend=backend,
        anki_connect_url=anki_connect_url,
        anki_base=anki_base,
    )


@anki_app.command("model-css")
def model_css(
    model: Annotated[str, typer.Argument(help="Model Name")],
    backend: Annotated[str | None, typer.Option(help="Force backend")] = None,
    anki_connect_url: Annotated[str | None, typer.Option(help="AnkiConnect URL Override")] = None,
    anki_base: Annotated[str | None, typer.Option(help="Anki Base Directory Override")] = None,
):
    """Get CSS styling for a model."""
    _run_anki_action(
        lambda anki: anki.get_model_styling(model),
        result_key="css",
        backend=backend,
        anki_connect_url=anki_connect_url,
        anki_base=anki_base,
    )


@anki_app.command("model-templates")
def model_templates(
    model: Annotated[str, typer.Argument(help="Model Name")],
    backend: Annotated[str | None, typer.Option(help="Force backend")] = None,
    anki_connect_url: Annotated[str | None, typer.Option(help="AnkiConnect URL Override")] = None,
    anki_base: Annotated[str | None, typer.Option(help="Anki Base Directory Override")] = None,
):
    """Get templates for a model."""
    _run_anki_action(
        lambda anki: anki.get_model_templates(model),
        result_key=None,
        backend=backend,
        anki_connect_url=anki_connect_url,
        anki_base=anki_base,
    )


@anki_app.command("browse")
def anki_browse(
    query: Annotated[str | None, typer.Option(help="Search query (e.g. 'nid:123')")] = None,
    nid: Annotated[int | None, typer.Option(help="Jump to Note ID")] = None,
    backend: Annotated[str | None, typer.Option(help="Force backend")] = None,
    anki_connect_url: Annotated[str | None, typer.Option(help="AnkiConnect URL Override")] = None,
    anki_base: Annotated[str | None, typer.Option(help="Anki Base Directory Override")] = None,
):
    """Open Anki browser."""
    if not query and not nid:
        typer.secho("Must specify --query or --nid", fg="red", err=True)
        raise typer.Exit(1)

    final_query = query or f"nid:{nid}"
    _run_anki_action(
        lambda anki: anki.gui_browse(final_query),
        backend=backend,
        anki_connect_url=anki_connect_url,
        anki_base=anki_base,
    )


@anki_app.command("decks")
def anki_decks(
    backend: Annotated[str | None, typer.Option(help="Force backend")] = None,
    anki_connect_url: Annotated[str | None, typer.Option(help="AnkiConnect URL Override")] = None,
    anki_base: Annotated[str | None, typer.Option(help="Anki Base Directory Override")] = None,
):
    """Print every deck name as JSON: {"decks": [...]}."""
    _run_anki_action(
        lambda anki: anki.get_deck_names(),
        result_key="decks",
        backend=backend,
        anki_connect_url=anki_connect_url,
        anki_base=anki_base,
    )


@anki_app.command("queue-deck")
def anki_queue_deck(
    ids: Annotated[
        str, typer.Option(help="Arete ids in queue order: a JSON list or comma-separated.")
    ],
    deck_name: Annotated[
        str, typer.Option(help="The filtered deck to fill.")
    ] = "Arete Study Queue",
    reschedule: Annotated[
        bool,
        typer.Option(
            "--reschedule/--no-reschedule", help="Let reviews in the deck count (Anki's setting)."
        ),
    ] = True,
    backend: Annotated[str | None, typer.Option(help="Force backend")] = None,
    anki_connect_url: Annotated[str | None, typer.Option(help="AnkiConnect URL Override")] = None,
    anki_base: Annotated[str | None, typer.Option(help="Anki Base Directory Override")] = None,
):
    """Fill a filtered deck with the cards of the given Arete ids, in order.

    Prints {"ok": bool, "message": str}.
    """
    from arete.application.queue.service import create_queue_deck

    arete_ids = _parse_ids(ids)

    async def make(anki):
        made = await create_queue_deck(anki, arete_ids, deck_name=deck_name, reschedule=reschedule)
        return {"ok": made.ok, "message": made.message}

    _run_anki_action(
        make,
        result_key=None,
        backend=backend,
        anki_connect_url=anki_connect_url,
        anki_base=anki_base,
    )
