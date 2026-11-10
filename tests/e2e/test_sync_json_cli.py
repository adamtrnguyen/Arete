"""`arete sync --json` against a real collection in tmp_path: one JSON object on stdout.

The Obsidian plugin reads this object to report a sync and to show a prune preview
before anything is deleted, so stdout must hold the object and nothing else.
"""

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from arete.interface.cli import app
from tests.e2e.conftest import open_collection

runner = CliRunner()


def _sync(config, *args: str):
    """Run `arete sync <args> --json` with `config`; return (exit code, report, stderr)."""
    with patch("arete.interface.cli._resolve_with_overrides", return_value=config):
        result = runner.invoke(app, ["sync", str(config.root_input), "--json", *args])
    return result.exit_code, json.loads(result.stdout), result.stderr


def _note_count(anki_base: Path) -> int:
    col = open_collection(anki_base)
    try:
        return len(col.find_notes(""))
    finally:
        col.close()


def test_a_sync_prints_one_report(vault, make_config, anki_base):
    code, report, stderr = _sync(make_config(vault))

    assert code == 0
    assert report == {
        "ok": True,
        "generated": 3,
        "changed": 3,
        "errors": 0,
        "message": None,
        "prune": None,
    }
    assert "Starting sync" in stderr
    assert _note_count(anki_base) == 3


def test_prune_previews_without_yes_and_deletes_with_it(vault, make_config, anki_base):
    _sync(make_config(vault))
    (vault / "Bayes.md").unlink()

    code, preview, _ = _sync(make_config(vault, prune=True), "--prune")

    assert code == 0
    prune = preview["prune"]
    assert prune["status"] == "unconfirmed"
    assert prune["note_count"] == 2
    assert prune["deck_count"] == 1
    assert prune["decks"] == ["Statistics"], "the top of the orphaned subtree"
    assert {n["deck"] for n in prune["notes"]} == {"Statistics::Inference"}
    assert all(n["arete_id"].startswith("arete_") and n["nid"] for n in prune["notes"])
    assert _note_count(anki_base) == 3, "a preview deletes nothing"

    code, done, _ = _sync(make_config(vault, prune=True), "--prune", "--yes")

    assert code == 0
    assert done["prune"]["status"] == "deleted"
    assert done["prune"]["note_count"] == 2
    assert _note_count(anki_base) == 1
