import logging
from pathlib import Path

from arete.application.utils.logging import (
    RunRecorder,
    rotate_logs,
    setup_logging,
    write_run_report,
)


def test_setup_logging(tmp_path):
    logger, log_path, run_id = setup_logging(tmp_path, verbose=1)

    assert isinstance(logger, logging.Logger)
    assert log_path.exists()
    assert log_path.parent == tmp_path
    assert run_id in log_path.name

    # Check handlers
    assert len(logger.handlers) == 2
    assert any(isinstance(h, logging.StreamHandler) for h in logger.handlers)
    assert any(isinstance(h, logging.FileHandler) for h in logger.handlers)


def test_run_recorder_add_inventory():
    recorder = RunRecorder()
    items = [
        {"nid": "1", "deck": "D1"},
        {"nid": None, "deck": "D2"},  # Valid deck, no NID
        {"nid": "2", "deck": "D1"},  # Duplicate deck
    ]
    recorder.add_inventory(items)

    assert recorder.inventory_nids == {"1", "2"}
    assert recorder.inventory_decks == {"D1", "D2"}


def test_write_run_report(tmp_path):
    recorder = RunRecorder()
    recorder.files_scanned = 5
    recorder.add_error(Path("bad.md"), "Syntax Error", "Line 1")

    run_id = "test_run"
    write_run_report(recorder, tmp_path, run_id)

    report_path = tmp_path / f"report_{run_id}.md"
    assert report_path.exists()

    content = report_path.read_text()
    assert "# Run Report [test_run]" in content
    assert "| Files Scanned | 5 |" in content
    assert "| `bad.md` | Syntax Error | Line 1 |" in content
    assert "## Warnings" not in content  # the recorder no longer collects warnings


def test_rotate_logs(tmp_path):
    # Create 5 dummy logs
    for i in range(5):
        (tmp_path / f"run_old_{i}.log").touch()
        # Ensure mtime diff
        (tmp_path / f"run_old_{i}.log").write_text(str(i))

    # Keep only 2
    rotate_logs(tmp_path, keep=2)

    # Check count
    logs = list(tmp_path.glob("run_*.log"))
    assert len(logs) == 2
