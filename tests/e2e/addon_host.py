"""The bundled AnkiConnect add-on's own code, run against a real collection without Anki's GUI.

The add-on (`arete_ankiconnect/`) imports `aqt` at module level, so it cannot be imported in a
test. Its action methods only touch the collection, though. This module compiles the parts it
needs straight from the add-on's source files (the `AnkiConnect` class, `util.api`,
`util.MediaType`, the reply formatters, `arete.get_obsidian_source`) and runs them against an
`anki.Collection` in `tmp_path`. Nothing is copied: an edit to the add-on is what runs here.

`AddonHost.http` has the signature of `AnkiConnectAdapter._http_request`, so the adapter can
talk to the add-on's real request handler with no socket in between.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import contextlib
import enum
import functools
import glob
import hashlib
import inspect
import json
import os
import re
import threading
import time
import types
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# anki.collection first: importing anki.notes or anki.cards before it is a circular import.
import anki
import anki._backend
import anki.collection
import anki.lang
import anki.notes
import anki.utils
from anki.cards import Card
from anki.collection import Collection
from anki.consts import MODEL_CLOZE
from anki.errors import NotFoundError
from anki.notes import Note

ADDON_DIR = Path(__file__).resolve().parents[2] / "arete_ankiconnect"


def load_addon_names(filename: str, names: list[str], namespace: dict[str, Any]) -> dict[str, Any]:
    """Compile only the named top-level definitions of an add-on source file into `namespace`."""
    path = ADDON_DIR / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if getattr(n, "name", None) in names]
    missing = set(names) - {getattr(n, "name", None) for n in nodes}
    if missing:
        raise LookupError(f"{filename} defines none of {sorted(missing)}")
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(module, str(path), "exec"), namespace)
    return namespace


def addon_get_obsidian_source():
    """The add-on's `_obsidian_source` parser, `arete_ankiconnect/arete.py:get_obsidian_source`."""
    return load_addon_names("arete.py", ["get_obsidian_source"], {})["get_obsidian_source"]


@functools.cache
def _addon_class() -> type:
    util_ns = load_addon_names("util.py", ["api", "MediaType"], {"enum": enum})
    web_ns = load_addon_names("web.py", ["format_success_reply", "format_exception_reply"], {})
    util = types.SimpleNamespace(
        api=util_ns["api"], MediaType=util_ns["MediaType"], setting=lambda _key: None
    )
    namespace: dict[str, Any] = {
        "anki": anki,
        # Module-level names the class reads, defined as the add-on's module defines them.
        "int_time": anki.utils.int_time,
        "field_checksum": anki.utils.field_checksum,
        "threading": threading,
        "util": util,
        "Card": Card,
        "Note": Note,
        "NotFoundError": NotFoundError,
        "MODEL_CLOZE": MODEL_CLOZE,
        "base64": base64,
        "glob": glob,
        "hashlib": hashlib,
        "inspect": inspect,
        "json": json,
        "os": os,
        "re": re,
        "time": time,
        "unicodedata": unicodedata,
        **web_ns,
    }
    return load_addon_names("__init__.py", ["AnkiConnect"], namespace)["AnkiConnect"]


class _Window:
    """The part of `aqt.mw` the add-on's collection actions reach: the collection and a reset."""

    def __init__(self, col: Collection):
        self.col = col

    def requireReset(self) -> None:
        """Anki redraws its UI here; there is no UI."""

    def maybeReset(self) -> None:
        """Anki redraws its UI here; there is no UI."""


_I18N: list[Any] = []


@contextlib.contextmanager
def _anki_i18n() -> Iterator[None]:
    """Set the translation backend Anki's GUI sets at start-up, for the call only.

    The add-on's duplicate check strips HTML through `anki.lang.current_i18n`, which only the
    GUI sets. It is restored afterwards, so the rest of the test session sees the library as
    the direct backend sees it in production.
    """
    if not _I18N:
        _I18N.append(anki._backend.RustBackend(langs=["en"]))
    saved = anki.lang.current_i18n
    anki.lang.current_i18n = _I18N[0]
    try:
        yield
    finally:
        anki.lang.current_i18n = saved


class AddonHost:
    """The add-on's request handler over an open collection, as Anki runs it."""

    def __init__(self, collection_path: Path):
        """Open the collection the add-on will act on; `close` releases it."""
        self.col = Collection(str(collection_path))
        cls = _addon_class()
        addon = cls.__new__(cls)
        # `__init__` is skipped because it starts the web server; set what it sets.
        addon.log = None
        addon.timer = None
        addon.refreshTimer = None
        addon.refreshPendingSince = 0.0
        addon.refreshing = False
        addon.actions = addon.buildActionTable()
        addon.window = lambda: _Window(self.col)
        # The add-on defers one UI refresh per burst of writes on a Qt timer; there is no UI.
        addon.scheduleRefresh = lambda: None
        self._addon = addon
        self.actions: list[str] = []

    async def http(self, payload: dict) -> Any:
        """Answer one AnkiConnect request with the add-on's own handler."""
        await asyncio.sleep(0)  # a real transport yields, so concurrent callers interleave
        self.actions.append(payload.get("action", ""))
        if payload.get("action") == "multi":
            self.actions.extend(a.get("action", "") for a in payload["params"]["actions"])
        with _anki_i18n():
            return self._addon.handler(payload)

    def close(self) -> None:
        self.col.close()
