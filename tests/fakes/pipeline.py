"""Run the pipeline with its NoteSync replaced, for tests of the pipeline itself.

`run_pipeline` builds one `NoteSync` per run over the bridge and calls its `prefetch`
and `sync_notes`. A pipeline test that scripts what each batch returns hands an object
with those two methods to `syncing_through`; the bridge then serves prune only.
The decisions themselves are tested against the real NoteSync elsewhere
(`tests/application/sync/test_note_sync.py`, `tests/e2e`).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch


@contextmanager
def syncing_through(fake: Any) -> Iterator[MagicMock]:
    """Every `NoteSync(...)` the pipeline builds is `fake`; yields the patched class.

    A fake with a `dry_run` attribute gets the run's flag.
    """

    def build(_store: Any, _claims: Any = None, _logger: Any = None, dry_run: bool = False):
        if hasattr(fake, "dry_run"):
            fake.dry_run = dry_run
        return fake

    factory = MagicMock(side_effect=build)
    with patch("arete.application.sync.pipeline.NoteSync", factory):
        yield factory
