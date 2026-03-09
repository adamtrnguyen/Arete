"""Vault maintenance commands: validate, fix, format."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from arete.interface._common import _resolve_with_overrides

vault_app = typer.Typer(name="vault", help="Vault maintenance: validate, fix, format.")


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

    # File-not-found is always a hard error (exit 1 regardless of output mode)
    if not path.exists():
        result = validate_arete_file(path)
        if json_output:
            typer.echo(json.dumps(asdict(result)))
        else:
            typer.secho("File not found.", fg="red")
        raise typer.Exit(1)

    result = validate_arete_file(path)

    if json_output:
        typer.echo(json.dumps(asdict(result)))
    else:
        if result.ok:
            typer.secho("✅ Valid arete file!", fg="green")
            typer.echo(f"  Deck: {result.stats['deck']}")
            typer.echo(f"  Cards: {result.stats['cards_found']}")
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
    from arete.application.utils.text import apply_fixes, validate_frontmatter

    if not path.exists():
        typer.secho("File not found.", fg="red")
        raise typer.Exit(1)

    content = path.read_text(encoding="utf-8")
    fixed_content = apply_fixes(content)

    if fixed_content == content:
        typer.secho("✅ No fixable issues found.", fg="green")
        valid_meta = bool(validate_frontmatter(content))
        if not valid_meta:
            typer.secho(
                "  (Note: File still has validation errors that cannot be auto-fixed)", fg="yellow"
            )
    else:
        path.write_text(fixed_content, encoding="utf-8")
        typer.secho("✨ File auto-fixed!", fg="green")
        typer.echo("  - Replaced tabs with spaces")
        typer.echo("  - Added missing cards list (if applicable)")


@vault_app.command("format")
def format_cmd(
    ctx: typer.Context,
    path: Annotated[
        Path | None,
        typer.Argument(help="Path to vault or file. Defaults to config."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview changes without saving.")
    ] = False,
):
    """[bold blue]Format[/bold blue] YAML frontmatter in your vault.

    Normalize serialization to use stripped block scalars (|-).
    """
    from arete.application.factory import get_vault_service

    verbose = ctx.obj.get("verbose_bonus", 1) if ctx.obj else 1
    config = _resolve_with_overrides(
        root_input=path,
        dry_run=dry_run,
        verbose=verbose,
    )
    vault = get_vault_service(config)

    typer.echo(f"✨ Formatting vault: {config.vault_root}")
    count = vault.format_vault(dry_run=dry_run)

    if dry_run:
        typer.secho(f"\n[DRY RUN] Would have formatted {count} files.", fg="yellow")
    else:
        typer.secho(f"\n✅ Formatted {count} files.", fg="green")
