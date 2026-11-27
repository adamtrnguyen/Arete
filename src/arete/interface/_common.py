"""Shared utilities for all CLI submodules."""

import contextlib
import io
import logging
import sys
from collections.abc import Iterator

import typer

from arete.application.config import AppConfig, resolve_config


def _resolve_with_overrides(**kwargs) -> AppConfig:
    """Build config from keyword overrides, filtering out None values."""
    return resolve_config({k: v for k, v in kwargs.items() if v is not None})


def get_verbose_override(ctx: typer.Context) -> int | None:
    """Return the explicit CLI verbosity override from the root callback, or None."""
    cur = ctx
    while cur is not None:
        if isinstance(cur.obj, dict) and "verbose" in cur.obj:
            return cur.obj["verbose"]
        cur = cur.parent
    return None


def configure_cli_logging(verbose_count: int) -> None:
    """Attach a single stderr handler on the `arete` logger without duplicating sync logs.

    Non-sync commands log through this handler (INFO by default, DEBUG when `-v` is passed).
    `arete sync` calls `setup_logging()`, which replaces handlers on `logging.getLogger("arete")`
    with its console + file handlers, so keeping the default handler on `arete` (not root)
    avoids duplicate log lines while keeping propagation intact for pytest `caplog`.
    """
    level = logging.DEBUG if verbose_count > 0 else logging.INFO
    arete_logger = logging.getLogger("arete")
    arete_logger.setLevel(level)
    if not arete_logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        arete_logger.addHandler(handler)
    for handler in arete_logger.handlers:
        handler.setLevel(level)


@contextlib.contextmanager
def stdout_to_stderr() -> Iterator[None]:
    """Send what runs inside to stderr.

    A command that prints JSON for the Obsidian plugin does its work inside this, so a
    library that prints (Anki's deprecation notices do) cannot put text before the JSON.
    """
    with contextlib.redirect_stdout(sys.stderr):
        yield


def utf8_console() -> None:
    """Print UTF-8 on stdout and stderr, whatever the console or pipe would encode.

    On Windows a pipe (how the Obsidian plugin runs `arete`) encodes with the ANSI code
    page, cp1252 on most machines, which cannot hold card text, note names or the arrow
    and check marks the CLI prints: such a print raised UnicodeEncodeError. The plugin
    decodes UTF-8. A stream already in UTF-8 is left as it is; anything still unencodable
    (a lone surrogate) is replaced, never raised.
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
