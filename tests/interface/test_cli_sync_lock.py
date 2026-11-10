"""`arete sync` while another sync holds the lock: exit 75, one line on stderr, nothing touched.

Both are real processes: one holds the lock on the cache, the other is the CLI. The CLI is
pointed at throwaway paths for everything (HOME, cache, logs, Anki, media) and at a closed
port for AnkiConnect, so it can reach no real collection whatever happens.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from arete.composition.orchestrator import EXIT_SYNC_LOCKED, SYNC_LOCKED_MESSAGE
from arete.infrastructure.persistence.sync_lock import lock_path_for
from tests.infrastructure.persistence.test_sync_lock import held_by_another_process

CARD = "---\narete: true\ndeck: D\ncards:\n- Front: Q\n  Back: A\n---\n\nBody.\n"


def _env(root: Path) -> dict[str, str]:
    home = root / "home"
    home.mkdir()
    return {
        **os.environ,
        "HOME": str(home),
        "ARETE_CACHE_DB": str(root / "cache" / "cache.db"),
        "ARETE_LOG_DIR": str(root / "logs"),
        "ARETE_ANKI_BASE": str(root / "anki"),
        "ARETE_ANKI_MEDIA_DIR": str(root / "media"),
        "ARETE_VAULT_ROOT": str(root / "vault"),
        "ARETE_ANKI_CONNECT_URL": "http://127.0.0.1:9",
    }


def test_a_sync_refused_by_the_lock_exits_75_and_touches_nothing(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "card.md").write_text(CARD, encoding="utf-8")
    env = _env(tmp_path)
    lock = lock_path_for(Path(env["ARETE_CACHE_DB"]))

    with held_by_another_process(lock):
        run = subprocess.run(
            [sys.executable, "-m", "arete", "sync", str(vault)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    assert run.returncode == EXIT_SYNC_LOCKED, run.stderr
    refusals = [line for line in run.stderr.splitlines() if SYNC_LOCKED_MESSAGE in line]
    assert refusals == [f"{SYNC_LOCKED_MESSAGE} (lock: {lock})"]
    assert (vault / "card.md").read_text(encoding="utf-8") == CARD, "no id was written"
    assert not Path(env["ARETE_CACHE_DB"]).exists(), "the cache was not opened"
    assert not (tmp_path / "logs").exists(), "no run was logged"
    assert not (tmp_path / "anki").exists(), "Anki was not opened"
