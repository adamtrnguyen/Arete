"""Shared utilities for all CLI submodules."""

import contextlib
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
