"""PL1: the plugin's graph views read `arete graph export` (CLI mode) or POST /graph."""

import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from arete.interface.cli import app as cli_app
from arete.interface.http_server import app as http_app

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


def test_http_route_returns_the_same_graph(tmp_path):
    res = TestClient(http_app).post("/graph", json={"vault_root": str(_vault(tmp_path))})
    assert res.status_code == 200
    assert res.json()["requires"] == [["arete_T", "arete_B"]]
