"""CLI integration tests — typer.testing.CliRunner against real Anki."""

import re

from typer.testing import CliRunner

from arete.interface.cli import app

runner = CliRunner()


def test_cli_sync(vault_factory, anki_url, setup_anki):
    """Basic CLI sync via CliRunner."""
    vault = vault_factory(
        {
            "cli_test.md": """\
---
deck: IntegrationTest
arete: true
cards:
  - nid: null
    Front: CLI Sync Card
    Back: Via CliRunner
---
"""
        }
    )
    result = runner.invoke(
        app,
        ["-v", "sync", str(vault), "--anki-connect-url", anki_url, "--clear-cache"],
    )
    assert result.exit_code == 0, f"CLI failed:\n{result.output}"
    assert "updated/added=" in result.output or "summary" in result.output

    content = (vault / "cli_test.md").read_text()
    assert re.search(r"nid:\s*['\"]?\d+['\"]?", content), "NID not written back"


def test_cli_sync_dry_run(vault_factory, anki_url, setup_anki):
    """CLI sync with --dry-run produces no mutations."""
    vault = vault_factory(
        {
            "dry.md": """\
---
deck: IntegrationTest
arete: true
cards:
  - nid: null
    Front: Dry Run Card
    Back: Should not sync
---
"""
        }
    )
    result = runner.invoke(
        app,
        [
            "-v",
            "sync",
            str(vault),
            "--anki-connect-url",
            anki_url,
            "--dry-run",
            "--clear-cache",
        ],
    )
    assert result.exit_code == 0, f"CLI failed:\n{result.output}"

    content = (vault / "dry.md").read_text()
    assert not re.search(r"nid:\s*['\"]?\d+['\"]?", content), "NID should not appear in dry-run"


def test_cli_vault_check_valid(tmp_path):
    """Arete vault check on a valid file exits 0."""
    md = tmp_path / "valid.md"
    md.write_text(
        """\
---
deck: Test
arete: true
cards:
  - nid: null
    Front: Valid
    Back: Card
---
""",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["vault", "check", str(md)])
    assert result.exit_code == 0, f"Expected valid:\n{result.output}"
    assert "Valid" in result.output or "valid" in result.output.lower()


def test_cli_vault_check_invalid(tmp_path):
    """Arete vault check on an invalid file exits 1."""
    md = tmp_path / "invalid.md"
    md.write_text(
        """\
---
arete: true
cards:
  - nid: null
---
""",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["vault", "check", str(md)])
    assert result.exit_code == 1, f"Expected failure:\n{result.output}"


def test_cli_config_show():
    """Arete config show exits 0 and outputs JSON."""
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0, f"Config show failed:\n{result.output}"
    assert "{" in result.output  # JSON output


def test_cli_graph_check(vault_factory):
    """Arete graph check on a small vault exits 0."""
    vault = vault_factory(
        {
            "a.md": """\
---
deck: Test
arete: true
cards:
  - id: arete_GRAPHA0000000000000001
    nid: null
    Front: A
    Back: A answer
---
""",
            "b.md": """\
---
deck: Test
arete: true
cards:
  - id: arete_GRAPHB0000000000000001
    nid: null
    Front: B
    Back: B answer
    deps:
      requires:
        - arete_GRAPHA0000000000000001
---
""",
        }
    )
    result = runner.invoke(app, ["graph", "check", str(vault)])
    assert result.exit_code == 0, f"Graph check failed:\n{result.output}"
    assert "Nodes:" in result.output or "Cycles: 0" in result.output
