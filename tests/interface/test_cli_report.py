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
    """Records unsuspend calls; can be told to fail."""

    def __init__(self, fail: Exception | None = None):
        """Start with nothing unsuspended; `fail` is raised by every unsuspend."""
        self.unsuspended: list[list[int]] = []
        self.closed = False
        self.fail = fail

    async def unsuspend_cards(self, cids):
        if self.fail:
            raise self.fail
        self.unsuspended.append(list(cids))
        return True

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
    assert "arete anki unsuspend 11 22" in result.output
    assert _read(reports_file) == []  # the reports are cleared even though the unsuspend failed
    assert bridge.closed
