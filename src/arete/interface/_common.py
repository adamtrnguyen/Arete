"""Shared utilities for all CLI submodules."""

import contextlib
import io
import sys
from collections.abc import Iterator

from arete.application.config import AppConfig, resolve_config


def _resolve_with_overrides(**kwargs) -> AppConfig:
    """Build config from keyword overrides, filtering out None values."""
    return resolve_config({k: v for k, v in kwargs.items() if v is not None})


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
