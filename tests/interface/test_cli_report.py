"""`arete report`: list, JSON output, and clearing reported cards (with unsuspend)."""

import json

import pytest
from typer.testing import CliRunner

from arete.application import report_service
from arete.interface.cli import app

runner = CliRunner()


def _report(n: int, cid: int | None = None, **extra) -> dict:
    r = {
        "file_path": f"notes/n{n}.md",
        "line": 10 * n,
        "arete_id": f"arete_{n}",
        "front": f"Front {n}",
        "note": f"Issue {n}",
        "timestamp": "2026-03-04T05:06:07+00:00",
    }
    if cid is not None:
        r["cid"] = cid
    r.update(extra)
    return r


@pytest.fixture
def reports_file(tmp_path, monkeypatch):
    path = tmp_path / "reports.json"
    monkeypatch.setattr(report_service, "REPORTS_PATH", path)
    return path


def _write(path, reports) -> None:
    path.write_text(json.dumps(reports), encoding="utf-8")


def _read(path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


class FakeBridge:
    """Records unsuspend calls; can be told to fail or return False."""

    def __init__(self, fail: Exception | None = None, ok: bool = True):
        """Start with nothing unsuspended; `fail` raises, `ok=False` returns False."""
        self.unsuspended: list[list[int]] = []
        self.closed = False
        self.fail = fail
        self.ok = ok

    async def unsuspend_cards(self, cids):
        if self.fail:
            raise self.fail
        self.unsuspended.append(list(cids))
        return self.ok

    async def close(self):
        self.closed = True


@pytest.fixture
def bridge(monkeypatch):
    b = FakeBridge()

    async def fake_get_bridge(_config):
        return b

    monkeypatch.setattr("arete.composition.factory.get_anki_bridge", fake_get_bridge)
    return b


# --- service ---


def test_load_reports_missing_file_is_empty(reports_file):
    assert report_service.load_reports() == []


def test_load_reports_non_list_is_empty(reports_file):
    reports_file.write_text('{"a": 1}', encoding="utf-8")
    assert report_service.load_reports() == []


def test_load_reports_corrupt_json_warns_and_is_empty(reports_file, caplog):
    reports_file.write_text("{not json", encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert report_service.load_reports() == []
    assert "cannot read" in caplog.text


def test_clear_reports_empty_file_returns_nothing(reports_file):
    assert report_service.clear_reports() == []
    assert not reports_file.exists()


def test_clear_reports_all_returns_cleared_and_empties_file(reports_file):
    reports = [_report(1), _report(2)]
    _write(reports_file, reports)
    assert report_service.clear_reports() == reports
    assert _read(reports_file) == []


def test_clear_reports_by_index_is_one_based_and_keeps_the_rest(reports_file):
    reports = [_report(1), _report(2), _report(3)]
    _write(reports_file, reports)
    assert report_service.clear_reports([2]) == [reports[1]]
    assert _read(reports_file) == [reports[0], reports[2]]


def test_clear_reports_out_of_range_index_changes_nothing(reports_file):
    reports = [_report(1), _report(2)]
    _write(reports_file, reports)
    before = reports_file.read_text(encoding="utf-8")
    assert report_service.clear_reports([0, 3, -1]) == []
    assert reports_file.read_text(encoding="utf-8") == before


# --- listing ---


def test_report_none_says_so(reports_file):
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 0
    assert "No reported cards." in result.stdout


def test_report_lists_each_with_location_text_and_trimmed_time(reports_file):
    _write(reports_file, [_report(1), _report(2, arete_id="")])
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 0
    out = result.stdout
    assert "Reported cards: 2" in out
    assert "1. notes/n1.md:10 (arete_1)" in out
    assert "   Front: Front 1" in out
    assert "   Issue: Issue 1" in out
    assert "   When:  2026-03-04 05:06" in out
    assert "2. notes/n2.md:20\n" in out  # no id part when the id is empty


def test_report_missing_fields_fall_back_to_placeholders(reports_file):
    _write(reports_file, [{"front": "F"}])
    result = runner.invoke(app, ["report"])
    assert "1. ?:?" in result.stdout
    assert "When:  ?" in result.stdout


def test_report_json_prints_the_stored_reports(reports_file):
    reports = [_report(1, front="café")]
    _write(reports_file, reports)
    result = runner.invoke(app, ["report", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == reports
    assert "café" in result.stdout  # not \u-escaped


def test_report_json_empty_prints_empty_json_list(reports_file):
    result = runner.invoke(app, ["report", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


# --- clearing ---


def test_report_clear_with_nothing_stored_warns(reports_file):
    result = runner.invoke(app, ["report", "--clear", "0"])
    assert result.exit_code == 0
    assert "No reports to clear." in result.stdout


def test_report_clear_without_cids_does_not_touch_anki(reports_file, bridge):
    _write(reports_file, [_report(1), _report(2)])
    result = runner.invoke(app, ["report", "--clear", "1"])
    assert result.exit_code == 0
    assert "Cleared 1 report(s)." in result.stdout
    assert bridge.unsuspended == []
    assert [r["arete_id"] for r in _read(reports_file)] == ["arete_2"]


def test_report_clear_all_unsuspends_every_reported_card(reports_file, bridge):
    _write(reports_file, [_report(1, cid=11), _report(2, cid=22)])
    result = runner.invoke(app, ["report", "--clear", "0"])
    assert result.exit_code == 0
    assert "Cleared 2 report(s), unsuspended 2 card(s)." in result.stdout
    assert bridge.unsuspended == [[11, 22]]
    assert bridge.closed
    assert _read(reports_file) == []


def test_report_clear_one_keeps_card_suspended_if_another_report_names_it(reports_file, bridge):
    _write(reports_file, [_report(1, cid=11), _report(2, cid=11), _report(3, cid=33)])
    result = runner.invoke(app, ["report", "--clear", "1"])
    assert result.exit_code == 0
    assert "Cleared 1 report(s)." in result.stdout
    assert bridge.unsuspended == []  # cid 11 is still reported by the second entry


def test_report_clear_unsuspend_failure_exits_1_and_names_the_cards(reports_file, bridge):
    bridge.fail = RuntimeError("anki is closed")
    _write(reports_file, [_report(1, cid=11), _report(2, cid=22)])
    result = runner.invoke(app, ["report", "--clear", "0"])
    assert result.exit_code == 1
    assert "unsuspending 2 card(s) FAILED: anki is closed" in result.output
    assert "arete anki unsuspend --cids 11,22" in result.output
    assert _read(reports_file) == []  # the reports are cleared even though the unsuspend failed
    assert bridge.closed


@pytest.mark.asyncio
async def test_clear_and_reconcile_reports_uses_anki_admin_port(reports_file):
    from contextlib import asynccontextmanager

    from arete.domain.interfaces import AnkiAdminPort

    class _FakeAdmin(AnkiAdminPort):
        def __init__(self):
            self.unsuspended: list[list[int]] = []

        async def get_deck_names(self) -> list[str]:
            return []

        async def gui_browse(self, query: str) -> bool:
            return True

        async def suspend_cards(self, cids: list[int]) -> bool:
            return True

        async def unsuspend_cards(self, cids: list[int]) -> bool:
            self.unsuspended.append(list(cids))
            return True

        async def get_model_styling(self, model_name: str) -> str:
            return ""

        async def get_model_templates(self, model_name: str) -> dict[str, dict[str, str]]:
            return {}

    admin = _FakeAdmin()

    @asynccontextmanager
    async def open_anki():
        yield admin

    _write(reports_file, [_report(1, cid=10), _report(2, cid=20)])
    outcome = await report_service.clear_and_reconcile_reports([1], open_anki)
    assert outcome.unsuspended is True
    assert outcome.to_unsuspend == [10]
    assert admin.unsuspended == [[10]]


def test_report_clear_unsuspend_returning_false_exits_1(reports_file, bridge):
    bridge.ok = False
    _write(reports_file, [_report(1, cid=11), _report(2, cid=22)])
    result = runner.invoke(app, ["report", "--clear", "0"])
    assert result.exit_code == 1
    assert "unsuspending 2 card(s) FAILED" in result.output
    assert "arete anki unsuspend --cids 11,22" in result.output
    assert bridge.closed


def test_report_clear_all_deduplicates_repeated_cids(reports_file, bridge):
    _write(reports_file, [_report(1, cid=11), _report(2, cid=11), _report(3, cid=22)])
    result = runner.invoke(app, ["report", "--clear", "0"])
    assert result.exit_code == 0
    assert "Cleared 3 report(s), unsuspended 2 card(s)." in result.stdout
    assert bridge.unsuspended == [[11, 22]]


def test_report_clear_json_mode_and_backend_override(reports_file, monkeypatch):
    seen_configs = []
    b = FakeBridge()

    async def fake_get_bridge(config):
        seen_configs.append(config)
        return b

    monkeypatch.setattr("arete.composition.factory.get_anki_bridge", fake_get_bridge)
    _write(reports_file, [_report(1, cid=42)])
    result = runner.invoke(
        app,
        ["report", "--clear", "0", "--json", "--backend", "direct"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["unsuspended"] is True
    assert payload["to_unsuspend"] == [42]
    assert seen_configs[0].backend == "direct"

    b.ok = False
    _write(reports_file, [_report(1, cid=43)])
    failed = runner.invoke(app, ["report", "--clear", "0", "--json"])
    assert failed.exit_code == 1
    fail_payload = json.loads(failed.stdout)
    assert fail_payload["unsuspended"] is False
    assert fail_payload["unsuspend_error"] is not None
