"""Vault maintenance commands: validate, fix, format."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from arete.application.config import VaultRootUnknown
from arete.interface._common import (
    _resolve_with_overrides,
    get_verbose_override,
    stdout_to_stderr,
)

vault_app = typer.Typer(
    name="vault",
    help="Vault maintenance: validate, fix, format.",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@vault_app.command("check")
def check(
    path: Annotated[Path, typer.Argument(help="Path to the markdown file to check.")],
    json_output: Annotated[bool, typer.Option("--json", help="Output results as JSON.")] = False,
):
    """Validate a single file for arete compatibility.

    Check YAML syntax and required fields.
    """
    from arete.application.validation import validate_arete_file

    # Missing file or directory is always a hard error (exit 1 regardless of output mode)
    if not path.exists():
        result = validate_arete_file(path)
        if json_output:
            typer.echo(json.dumps(asdict(result)))
        else:
            typer.secho("File not found.", fg="red", err=True)
        raise typer.Exit(1)

    if path.is_dir():
        msg = "Path is a directory, expected a markdown file."
        if json_output:
            payload = {"ok": False, "errors": [{"line": 0, "message": msg}], "stats": {}}
            typer.echo(json.dumps(payload))
        else:
            typer.secho(msg, fg="red", err=True)
        raise typer.Exit(1)

    if json_output:
        with stdout_to_stderr():
            result = validate_arete_file(path)
        typer.echo(json.dumps(asdict(result)))
        return

    result = validate_arete_file(path)
    if result.ok:
        typer.secho("✅ Valid arete file!", fg="green")
        typer.echo(f"  Deck: {result.stats['deck']}")
        typer.echo(f"  Cards: {result.stats['cards_found']}")
        typer.echo(
            "  Ids shared with other files are not checked here: "
            "`arete graph check` reports them and a sync refuses them."
        )
    else:
        typer.secho("❌ Validation Failed:", fg="red")
        for err in result.errors:
            loc = f"L{err.get('line', '?')}"
            typer.echo(f"  [{loc}] {err['message']}")
        raise typer.Exit(1)


@vault_app.command("fix")
def fix(
    path: Annotated[Path, typer.Argument(help="Path to the markdown file to fix.")],
):
    """Attempt to automatically fix common format errors in a file."""
    from arete.application.utils.fs import read_text_and_newline, replace_text
    from arete.application.utils.text import apply_fixes
    from arete.application.validation import validate_arete_file

    if not path.exists():
        typer.secho("File not found.", fg="red", err=True)
        raise typer.Exit(1)

    if path.is_dir():
        typer.secho("Path is a directory, expected a markdown file.", fg="red", err=True)
        raise typer.Exit(1)

    content, newline = read_text_and_newline(path)
    fixed_content = apply_fixes(content)

    if fixed_content == content:
        typer.secho("✅ No fixable issues found.", fg="green")
        if not validate_arete_file(path).ok:
            typer.secho(
                "  (Note: File still has validation errors that cannot be auto-fixed)", fg="yellow"
            )
    else:
        replace_text(path, fixed_content, newline)
        typer.secho("✨ File auto-fixed!", fg="green")
        typer.echo("  Normalized YAML frontmatter.")


@vault_app.command("format")
def format_cmd(
    ctx: typer.Context,
    path: Annotated[
        Path | None,
        typer.Argument(help="Path to vault or file. Defaults to config."),
    ] = None,
    dry_run: Annotated[
        bool | None,
        typer.Option("--dry-run/--no-dry-run", help="Preview changes without saving."),
    ] = None,
):
    """[bold blue]Format[/bold blue] YAML frontmatter in your vault.

    Normalize serialization to use stripped block scalars (|-).
    """
    from arete.composition.factory import opened_vault_service

    if path is not None and not path.exists():
        typer.secho(f"Error: path not found: {path}", fg="red", err=True)
        raise typer.Exit(1)

    config = _resolve_with_overrides(
        root_input=path,
        dry_run=dry_run,
        verbose=get_verbose_override(ctx),
    )
    try:
        with opened_vault_service(config) as vault:
            typer.echo(f"✨ Formatting vault: {vault.root}")
            count = vault.format_vault(dry_run=config.dry_run)
    except VaultRootUnknown as e:
        typer.secho(f"Error: {e}", fg="red", err=True)
        raise typer.Exit(1) from e

    if config.dry_run:
        typer.secho(f"\n[DRY RUN] Would have formatted {count} files.", fg="yellow")
    else:
        typer.secho(f"\n✅ Formatted {count} files.", fg="green")
