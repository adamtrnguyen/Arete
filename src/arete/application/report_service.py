"""Service for reading and managing card issue reports."""

import json
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path

from arete.application.utils.fs import replace_text
from arete.domain.interfaces import AnkiAdminPort

logger = logging.getLogger(__name__)

REPORTS_PATH = Path.home() / ".config" / "arete" / "reports.json"


@dataclass(frozen=True)
class ClearReportsResult:
    """Outcome of clearing stored reports and reconciling Anki card suspension."""

    cleared: list[dict]
    to_unsuspend: list[int]
    unsuspended: bool = False
    unsuspend_error: str | None = None


def load_reports() -> list[dict]:
    """Load all reports from the reports file."""
    if not REPORTS_PATH.exists():
        return []
    try:
        data = json.loads(REPORTS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[reports] cannot read {REPORTS_PATH}, treating as empty: {e}")
        return []


def clear_reports(indices: list[int] | None = None) -> list[dict]:
    """Remove reports by 1-based index, or all if indices is None.

    Returns the list of cleared report dicts.
    """
    reports = load_reports()
    if not reports:
        return []

    if indices is None:
        REPORTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        replace_text(REPORTS_PATH, "[]")
        return reports

    # Convert 1-based indices to 0-based, filter valid
    to_remove = {i - 1 for i in indices if 1 <= i <= len(reports)}
    if not to_remove:
        return []

    cleared = [r for idx, r in enumerate(reports) if idx in to_remove]
    remaining = [r for idx, r in enumerate(reports) if idx not in to_remove]
    REPORTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    replace_text(REPORTS_PATH, json.dumps(remaining, indent=2, ensure_ascii=False))
    return cleared


async def clear_and_reconcile_reports(
    indices: list[int] | None,
    open_anki: Callable[[], AbstractAsyncContextManager[AnkiAdminPort]],
) -> ClearReportsResult:
    """Clear matching reports and unsuspend cards no longer referenced by any remaining report."""
    cleared = clear_reports(indices)
    if not cleared:
        return ClearReportsResult(cleared=[], to_unsuspend=[])

    remaining = load_reports()
    remaining_cids = {r["cid"] for r in remaining if r.get("cid")}
    to_unsuspend = [r["cid"] for r in cleared if r.get("cid") and r["cid"] not in remaining_cids]
    if not to_unsuspend:
        return ClearReportsResult(cleared=cleared, to_unsuspend=[])

    try:
        async with open_anki() as anki:
            await anki.unsuspend_cards(to_unsuspend)
    except Exception as e:
        return ClearReportsResult(
            cleared=cleared,
            to_unsuspend=to_unsuspend,
            unsuspended=False,
            unsuspend_error=str(e),
        )
    return ClearReportsResult(cleared=cleared, to_unsuspend=to_unsuspend, unsuspended=True)
