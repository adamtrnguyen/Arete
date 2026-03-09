import logging
import os
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class LogEntry:
    file: str
    message: str
    context: str | None = None


@dataclass
class RunRecorder:
    # Stats
    files_scanned: int = 0
    files_cached_meta: int = 0
    cards_generated: int = 0
    cards_cached_content: int = 0
    cards_synced: int = 0
    cards_failed: int = 0

    # Prune Mode Inventory (All valid NIDs/decks found in vault)
    inventory_nids: set = field(default_factory=set)
    inventory_decks: set = field(default_factory=set)
    _inventory_lock: threading.Lock = field(default_factory=threading.Lock)

    # Details
    errors: list[LogEntry] = field(default_factory=list)
    warnings: list[LogEntry] = field(default_factory=list)

    start_time: datetime = field(default_factory=datetime.now)

    def add_inventory(self, items: list[Any]):
        with self._inventory_lock:
            for item in items:
                nid = item.get("nid") if isinstance(item, dict) else getattr(item, "nid", None)
                deck = item.get("deck") if isinstance(item, dict) else getattr(item, "deck", None)
                if nid:
                    self.inventory_nids.add(str(nid))
                if deck:
                    self.inventory_decks.add(str(deck))

    def add_error(self, file: Path, msg: str, context: str | None = None):
        self.errors.append(LogEntry(file.name, msg, context))

    def add_warning(self, file: Path, msg: str, context: str | None = None):
        self.warnings.append(LogEntry(file.name, msg, context))


def setup_logging(log_dir: Path, verbose: int) -> tuple[logging.Logger, Path, str]:
    """Return (logger, log_path, run_id)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{os.getpid()}"
    main_log_path = log_dir / f"run_{run_id}.log"

    # Determine console level based on verbose flag
    console_level = (
        logging.WARNING if verbose == 0 else logging.INFO if verbose == 1 else logging.DEBUG
    )

    # Main logger: Capture EVERYTHING at root, handlers will filter.
    logger = logging.getLogger("arete")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    # Console handler (Filtered by verbosity)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(console_level)
    sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    # Main file handler (ALWAYS Debug to capture full trace in file)
    fh = logging.FileHandler(main_log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    logger.addHandler(sh)
    logger.addHandler(fh)

    return logger, main_log_path, run_id


def write_run_report(recorder: RunRecorder, log_dir: Path, run_id: str):
    report_path = log_dir / f"report_{run_id}.md"
    duration = datetime.now() - recorder.start_time

    lines = [
        f"# Run Report [{run_id}]",
        f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Duration**: {duration}",
        "",
        "## Summary",
        "| Metric | Count |",
        "|---|---|",
        f"| Files Scanned | {recorder.files_scanned} |",
        f"| Files Cached (Meta) | {recorder.files_cached_meta} |",
        f"| Cards Generated | {recorder.cards_generated} |",
        f"| Cards Cached (Content) | {recorder.cards_cached_content} |",
        f"| Cards Synced | {recorder.cards_synced} |",
        f"| Cards Failed | {recorder.cards_failed} |",
        "",
    ]

    if recorder.errors:
        lines.append("## Errors")
        lines.append("| File | Message | Context |")
        lines.append("|---|---|---|")
        for e in recorder.errors:
            ctx = e.context or ""
            lines.append(f"| `{e.file}` | {e.message} | {ctx} |")
        lines.append("")

    if recorder.warnings:
        lines.append("## Warnings")
        # Collapsible check if too many? For now just list them.
        for e in recorder.warnings:
            lines.append(f"- **{e.file}**: {e.message}")
        lines.append("")

    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        # Automatic rotation: Keep last 50
        rotate_logs(log_dir, keep=50)
    except Exception:
        pass


def rotate_logs(log_dir: Path, keep: int = 50):
    """Keep only the latest N run logs and reports."""
    try:
        # Sort by modification time (oldest first)
        logs = sorted(log_dir.glob("run_*.log"), key=os.path.getmtime)
        reports = sorted(log_dir.glob("report_*.md"), key=os.path.getmtime)

        # Delete excesses
        for f in logs[:-keep]:
            os.remove(str(f))
        for f in reports[:-keep]:
            os.remove(str(f))
    except Exception:
        # Silently fail if rotation has issues
        pass
