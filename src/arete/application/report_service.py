"""Service for reading and managing card issue reports."""

import json
from pathlib import Path

REPORTS_PATH = Path.home() / ".config" / "arete" / "reports.json"


def load_reports() -> list[dict]:
    """Load all reports from the reports file."""
    if not REPORTS_PATH.exists():
        return []
    try:
        data = json.loads(REPORTS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def clear_reports(indices: list[int] | None = None) -> list[dict]:
    """Remove reports by 1-based index, or all if indices is None.

    Returns the list of cleared report dicts.
    """
    reports = load_reports()
    if not reports:
        return []

    if indices is None:
        REPORTS_PATH.write_text("[]", encoding="utf-8")
        return reports

    # Convert 1-based indices to 0-based, filter valid
    to_remove = {i - 1 for i in indices if 1 <= i <= len(reports)}
    if not to_remove:
        return []

    cleared = [r for idx, r in enumerate(reports) if idx in to_remove]
    remaining = [r for idx, r in enumerate(reports) if idx not in to_remove]
    REPORTS_PATH.write_text(json.dumps(remaining, indent=2, ensure_ascii=False), encoding="utf-8")
    return cleared
