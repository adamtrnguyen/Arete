import contextlib
import json
import os
import re
import tempfile
import webbrowser
from datetime import UTC, datetime
from urllib.parse import quote

from aqt import gui_hooks, mw
from aqt.browser import Browser
from aqt.qt import QAction, QApplication, QInputDialog, QKeySequence, QMenu
from aqt.utils import showWarning, tooltip

from . import util

# ─────────────────────────────────────────────────────────────────────────────
# Arete Obsidian Integration & Card Reporting
# ─────────────────────────────────────────────────────────────────────────────

_MISSING_SOURCE_MSG = (
    "No Obsidian source found for this card.\n\n"
    "Make sure the card was synced with arete and has the "
    "'_obsidian_source' field."
)


def get_obsidian_source(note) -> tuple[str, str, int, str] | None:
    """Extract Obsidian source info from note's _obsidian_source field.

    Returns (vault_name, file_path, card_index, arete_id) or None if not found.
    """
    for field_name in note.keys():
        if field_name == "_obsidian_source":
            field_value = note[field_name]
            if field_value:
                # Format: vault|path|line|arete_id
                parts = field_value.strip().split("|")
                if len(parts) == 4:
                    vault, file_path, line, arete_id = parts
                    try:
                        card_idx = int(line)
                    except ValueError:
                        card_idx = 1
                    return vault, file_path, card_idx, arete_id
    return None


def _require_obsidian_source(
    note, *, use_tooltip: bool = False
) -> tuple[str, str, int, str] | None:
    source = get_obsidian_source(note)
    if not source:
        if use_tooltip:
            tooltip("No Obsidian source found for this card.")
        else:
            showWarning(_MISSING_SOURCE_MSG)
        return None
    return source


def open_obsidian_uri(vault: str, file_path: str, card_idx: int = 1) -> bool:
    """Open Obsidian via URI scheme.

    Returns True on success, False on failure.
    """
    encoded_vault = quote(vault)
    encoded_path = quote(file_path)

    # Use Advanced URI for line-level navigation (requires Advanced URI plugin in Obsidian)
    uri = f"obsidian://advanced-uri?vault={encoded_vault}&filepath={encoded_path}&line={card_idx}"

    try:
        webbrowser.open(uri)
        return True
    except Exception as e:
        showWarning(f"Failed to open Obsidian: {e}")
        return False


def open_current_card_in_obsidian():
    """Open current reviewing card's source in Obsidian."""
    reviewer = mw.reviewer
    if not reviewer or not reviewer.card:
        showWarning("No card is currently being reviewed.")
        return

    source = _require_obsidian_source(reviewer.card.note())
    if not source:
        return

    vault, file_path, card_idx, _arete_id = source
    if open_obsidian_uri(vault, file_path, card_idx):
        tooltip(f"Opening in Obsidian: {file_path}")


def copy_obsidian_source():
    """Copy current card's Obsidian source path and arete ID to clipboard."""
    reviewer = mw.reviewer
    if not reviewer or not reviewer.card:
        return

    source = _require_obsidian_source(reviewer.card.note(), use_tooltip=True)
    if not source:
        return

    _vault, file_path, _card_idx, arete_id = source

    try:
        vault_root = util.setting("vault_root") or ""
    except Exception:
        vault_root = ""

    if vault_root:
        vault_root = os.path.expanduser(vault_root)
    full_path = os.path.join(vault_root, file_path) if vault_root else file_path
    copy_text = f"{full_path} (card: {arete_id})" if arete_id else full_path

    clipboard = QApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(copy_text)
    tooltip(f"Copied: {file_path}" + (f" [{arete_id}]" if arete_id else ""))


REPORTS_PATH = os.path.join(os.path.expanduser("~"), ".config", "arete", "reports.json")


def _append_report_atomic(report: dict, reports_path: str | None = None) -> bool:
    """Append a report to reports_path atomically; refuse to overwrite corrupt files."""
    target = REPORTS_PATH if reports_path is None else reports_path
    reports_dir = os.path.dirname(target)
    os.makedirs(reports_dir, exist_ok=True)

    reports: list = []
    if os.path.exists(target):
        try:
            with open(target, encoding="utf-8") as f:
                loaded = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            showWarning(
                f"Cannot read {target}: {e}\n\n"
                "Fix or remove the corrupt reports file before reporting new cards."
            )
            return False
        if not isinstance(loaded, list):
            showWarning(
                f"{target} does not contain a JSON list.\n\n"
                "Fix or remove the corrupt reports file before reporting new cards."
            )
            return False
        reports = loaded

    reports.append(report)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=reports_dir,
            prefix=".reports.",
            suffix=".tmp",
            delete=False,
        ) as f:
            tmp_path = f.name
            json.dump(reports, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
        tmp_path = None
        return True
    except OSError as e:
        showWarning(f"Failed to save report to {target}: {e}")
        return False
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)


def report_current_card():
    """Report an issue with the current reviewing card."""
    reviewer = mw.reviewer
    if not reviewer or not reviewer.card:
        showWarning("No card is currently being reviewed.")
        return

    card = reviewer.card
    card_id = card.id
    note = card.note()
    source = _require_obsidian_source(note)
    if not source:
        return

    issue_note, ok = QInputDialog.getText(mw, "Report Card Issue", "What's the issue?")
    if not ok or not issue_note.strip():
        return

    _vault, file_path, card_idx, arete_id = source

    # Get front text from the note's first field, stripped of HTML
    fields = note.fields
    front_text = re.sub(r"<[^>]*>", "", fields[0]).strip() if fields else ""
    if len(front_text) > 80:
        front_text = front_text[:77] + "..."

    report = {
        "nid": note.id,
        "cid": card_id,
        "arete_id": arete_id,
        "file_path": file_path,
        "line": card_idx,
        "front": front_text,
        "note": issue_note.strip(),
        "timestamp": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
    }

    if not _append_report_atomic(report):
        return

    if mw.col is None:
        showWarning("Report saved, but no Anki collection is open to suspend the card.")
        return

    # Suspend the originally captured card (reviewer.card may have changed during dialog)
    mw.col.sched.suspend_cards([card_id])
    tooltip(f"Reported & suspended: {issue_note.strip()}")

    # Advance only if the reviewer is still showing the reported card
    if mw.reviewer and mw.reviewer.card and mw.reviewer.card.id == card_id:
        mw.reviewer.nextCard()


def setup_reviewer_shortcut():
    """Add keyboard shortcuts and menu items."""
    # Open in Obsidian (Ctrl+Shift+O = Cmd+Shift+O on Mac)
    action = QAction("Open in Obsidian", mw)
    action.setShortcut(QKeySequence("Ctrl+Shift+O"))
    action.triggered.connect(open_current_card_in_obsidian)
    mw.form.menuTools.addAction(action)

    # Copy source path (Ctrl+Shift+C = Cmd+Shift+C on Mac, only active during review)
    copy_action = QAction("Copy Obsidian Source", mw)
    copy_action.setShortcut(QKeySequence("Ctrl+Shift+C"))
    copy_action.triggered.connect(copy_obsidian_source)
    copy_action.setEnabled(False)
    mw.form.menuTools.addAction(copy_action)

    # Report card issue (Ctrl+Shift+R = Cmd+Shift+R on Mac)
    report_action = QAction("Report Card Issue", mw)
    report_action.setShortcut(QKeySequence("Ctrl+Shift+R"))
    report_action.triggered.connect(report_current_card)
    mw.form.menuTools.addAction(report_action)

    def on_state_change(new_state, old_state):
        copy_action.setEnabled(new_state == "review")

    gui_hooks.state_did_change.append(on_state_change)


def on_browser_context_menu(browser: Browser, menu: QMenu):
    """Add 'Open in Obsidian' to browser right-click menu."""
    selected = browser.selected_notes()
    if not selected:
        return

    action = menu.addAction("Open in Obsidian")
    action.triggered.connect(lambda: open_selected_notes_in_obsidian(browser))


def open_selected_notes_in_obsidian(browser: Browser):
    """Open selected notes in Obsidian (first one if multiple selected)."""
    selected = browser.selected_notes()
    if not selected:
        showWarning("No notes selected.")
        return

    if mw.col is None:
        showWarning("No Anki collection is open.")
        return

    # Open first selected note
    note_id = selected[0]
    note = mw.col.get_note(note_id)

    source = _require_obsidian_source(note)
    if not source:
        return

    vault, file_path, card_idx, _arete_id = source
    if open_obsidian_uri(vault, file_path, card_idx):
        tooltip(f"Opening in Obsidian: {file_path}")

    # If multiple selected, notify user
    if len(selected) > 1:
        tooltip(f"Opened first of {len(selected)} selected notes")
