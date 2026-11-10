"""The plugin's graph views read `arete graph export`."""

import json

from typer.testing import CliRunner

from arete.interface.cli import app as cli_app

NOTE = "---\narete: true\ncards:\n  - id: {cid}\n    Front: {front}\n    Back: a\n{deps}---\n"


def _vault(tmp_path):
    (tmp_path / "Base.md").write_text(NOTE.format(cid="arete_B", front="base", deps=""))
    (tmp_path / "Top.md").write_text(
        NOTE.format(cid="arete_T", front="top", deps="    deps:\n      requires: [Base]\n")
    )
    return tmp_path


def test_cli_prints_the_graph_as_json_only(tmp_path):
    result = CliRunner().invoke(cli_app, ["graph", "export", str(_vault(tmp_path))])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)  # the plugin parses stdout whole
    assert data["requires"] == [["arete_T", "arete_B"]]
    assert {n["file"] for n in data["nodes"]} == {"Base.md", "Top.md"}
