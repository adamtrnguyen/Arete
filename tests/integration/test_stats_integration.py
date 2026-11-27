"""Integration tests for `arete anki stats` against live AnkiConnect."""

import json
import re

from typer.testing import CliRunner

from arete.interface.cli import app


def test_stats_retrieval_over_ankiconnect(integration_vault, anki_url, setup_anki, run_arete):
    """Sync cards and verify `arete anki stats --backend ankiconnect` returns live stats."""
    sync_res = run_arete(integration_vault, anki_url)
    assert sync_res.returncode == 0, sync_res.stderr

    content = (integration_vault / "basic_card.md").read_text(encoding="utf-8")
    match = re.search(r"nid:\s*['\"]?(\d+)['\"]?", content)
    assert match, f"NID missing after sync:\n{content}"
    nid = match.group(1)

    result = CliRunner().invoke(
        app,
        [
            "anki",
            "stats",
            "--nids",
            nid,
            "--anki-connect-url",
            anki_url,
            "--backend",
            "ankiconnect",
        ],
    )
    assert result.exit_code == 0, result.output
    stats = json.loads(result.stdout)
    found = next((s for s in stats if str(s["note_id"]) == str(nid)), None)
    assert found, f"Stats for {nid} not found in {stats}"
    assert "interval" in found
    assert "due" in found
