"""Keep caller-supplied paths inside the configured vault.

The MCP and HTTP surfaces take paths from whoever is calling (an agent, or any process
that can reach the daemon's port), so every such path must resolve inside the vault.
See docs/history/2026-11-04-prune-and-path-containment.md.

The configured vault is `vault_root` from `~/.config/arete/config.toml` or the
`ARETE_VAULT_ROOT` environment variable (the Obsidian plugin sets it on the daemon it
starts).
It is never the caller's path and never the working directory. The user-run CLI does
not use this module: whoever types the command already has their own file access.
"""

from __future__ import annotations

from pathlib import Path

from arete.application.config import AppConfig


class PathRefusedError(ValueError):
    """A caller-supplied path is outside what is configured, or nothing is configured."""


def configured_vault_root() -> Path:
    """The vault the user configured. Raises PathRefusedError if there is none."""
    root = AppConfig().vault_root
    if root is None:
        raise PathRefusedError(
            "No vault is configured, so no path can be accepted. Set vault_root in "
            "~/.config/arete/config.toml, or the ARETE_VAULT_ROOT environment variable."
        )
    return root.resolve()


def within_vault(path: str | Path, vault_root: Path) -> Path:
    """Resolve `path` and return it if it is the vault or inside it; refuse otherwise.

    A relative path is taken relative to the vault. Both sides are resolved before
    they are compared, so symlinks (`/tmp` is `/private/tmp` on macOS) and `..` cannot
    walk out, and a vault path with spaces compares as it is.
    """
    root = vault_root.resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and not resolved.is_relative_to(root):
        raise PathRefusedError(f"Refused: {path} is outside the configured vault ({root}).")
    return resolved


def require_configured(requested: str | Path | None, configured: Path | None, what: str) -> None:
    """Refuse a caller-supplied path that differs from the configured one.

    For paths that are not in the vault at all, such as the Anki base folder: the
    direct backend unpickles `prefs21.db` from it, so a caller must not choose it.
    """
    if requested is None:
        return
    if configured is None or Path(requested).expanduser().resolve() != configured.resolve():
        raise PathRefusedError(
            f"Refused: {what} {requested} differs from the configured {what} ({configured})."
        )
