"""Run the pipeline with its NoteSync replaced via explicit DI, for tests of the pipeline itself.

`run_pipeline` accepts `note_sync_factory` so tests inject a fake NoteSync directly without
monkeypatching module globals; the bridge then serves prune only.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def note_sync_factory_for(fake: Any) -> Callable[..., Any]:
    """Return a `note_sync_factory` for `run_pipeline` that yields `fake`.

    A fake with a `dry_run` attribute receives the run's `dry_run` flag. A fake lacking
    `rewrite_fields` gets an async stub recording rewritten field updates.
    """
    if not hasattr(fake, "rewrite_fields"):
        fake.rewritten = []

        async def rewrite_fields(items: Any) -> dict:
            fake.rewritten.extend(items)
            return {}

        fake.rewrite_fields = rewrite_fields

    def build(_store: Any, _claims: Any = None, _logger: Any = None, dry_run: bool = False) -> Any:
        if hasattr(fake, "dry_run"):
            fake.dry_run = dry_run
        return fake

    return build
