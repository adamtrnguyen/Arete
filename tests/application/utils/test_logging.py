import logging
from types import SimpleNamespace

import pytest

from arete.application.utils.logging import (
    RunRecorder,
    close_run_log,
    rotate_logs,
    setup_logging,
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


def test_run_recorder_inventory_takes_mappings_only():
    with pytest.raises(AttributeError):
        RunRecorder().add_inventory([SimpleNamespace(nid="1", deck="D1")])  # type: ignore[arg-type]


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


def test_setup_logging_rotates_old_run_logs(tmp_path):
    """setup_logging calls rotate_logs, so run logs do not grow forever."""
    for i in range(60):
        (tmp_path / f"run_old_{i:02d}.log").write_text(str(i))
    logger, _, _ = setup_logging(tmp_path, verbose=0)
    for handler in logger.handlers:
        handler.close()
    # the 50 newest old logs survive, plus the one this run just opened
    assert len(list(tmp_path.glob("run_*.log"))) == 51


def test_close_run_log_detaches_and_closes_the_log_file(tmp_path):
    logger, log_path, _ = setup_logging(tmp_path, verbose=1)
    [file_handler] = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]

    close_run_log(logger)

    assert logger.handlers == []
    assert file_handler.stream is None, "the file is closed, so Windows can delete it"
    log_path.unlink()


def test_a_second_run_closes_the_first_runs_log_file(tmp_path):
    first, _, _ = setup_logging(tmp_path / "a", verbose=1)
    [old] = [h for h in first.handlers if isinstance(h, logging.FileHandler)]
    setup_logging(tmp_path / "b", verbose=1)
    assert old.stream is None
    close_run_log(first)
