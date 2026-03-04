import json
import os
import re
import webbrowser
from datetime import datetime, timezone
from urllib.parse import quote

from aqt import gui_hooks, mw
from aqt.browser import Browser
from aqt.qt import QAction, QApplication, QInputDialog, QKeySequence, QMenu
from aqt.utils import showWarning, tooltip

# ─────────────────────────────────────────────────────────────────────────────
# Arete Config & Logic
# ─────────────────────────────────────────────────────────────────────────────

ADDON_PATH = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(ADDON_PATH, "config.json")
CONFIG = {}


def load_config():
    global CONFIG
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                CONFIG = json.load(f)
        except Exception:
            CONFIG = {}


load_config()


def get_obsidian_source(note) -> tuple[str, str, int, str] | None:
    """
    Extract Obsidian source info from note's _obsidian_source field.
    Returns (vault_name, file_path, card_index, arete_id) or None if not found.
    """
    for field_name in note.keys():
        if field_name == "_obsidian_source":
            field_value = note[field_name]
            if field_value:
                # Strip HTML tags if any (legacy sync issues)
                clean_value = re.sub(r"<[^>]*>", "", field_value).strip()

                # Format: vault|path|line|arete_id
                parts = clean_value.split("|")
                if len(parts) >= 3:
                    vault = parts[0]
                    file_path = parts[1]
                    try:
                        card_idx = int(parts[2])
                    except ValueError:
                        card_idx = 1
                    arete_id = parts[3] if len(parts) >= 4 else ""
                    return vault, file_path, card_idx, arete_id
    return None


def open_obsidian_uri(vault: str, file_path: str, card_idx: int = 1) -> bool:
    """
    Open Obsidian via URI scheme.
    Returns True on success, False on failure.
    """
    # Allow config to override vault name
    actual_vault = CONFIG.get("vault_name_override", vault) or vault

    encoded_vault = quote(actual_vault)
    encoded_path = quote(file_path)

    # Use Advanced URI for line-level navigation (requires Advanced URI plugin in Obsidian)
    uri = f"obsidian://advanced-uri?vault={encoded_vault}&filepath={encoded_path}&line={card_idx}"

    # Fallback: Standard URI (no line navigation, but works without plugins)
    # uri = f"obsidian://open?vault={encoded_vault}&file={encoded_path}"

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

    note = reviewer.card.note()
    source = get_obsidian_source(note)

    if not source:
        showWarning(
            "No Obsidian source found for this card.\n\n"
            "Make sure the card was synced with arete and has the "
            "'_obsidian_source' field."
        )
        return

    vault, file_path, card_idx, _arete_id = source
    if open_obsidian_uri(vault, file_path, card_idx):
        tooltip(f"Opening in Obsidian: {file_path}")


def copy_obsidian_source():
    """Copy current card's Obsidian source path and arete ID to clipboard."""
    reviewer = mw.reviewer
    if not reviewer or not reviewer.card:
        return

    note = reviewer.card.note()
    source = get_obsidian_source(note)

    if not source:
        tooltip("No Obsidian source found for this card.")
        return

    vault, file_path, card_idx, arete_id = source

    vault_root = CONFIG.get("vault_root", "")
    if vault_root:
        full_path = os.path.join(vault_root, file_path)
    else:
        full_path = file_path

    if arete_id:
        copy_text = f"{full_path} (card: {arete_id})"
    else:
        copy_text = full_path

    QApplication.clipboard().setText(copy_text)
    tooltip(f"Copied: {file_path}" + (f" [{arete_id}]" if arete_id else ""))


REPORTS_PATH = os.path.join(os.path.expanduser("~"), ".config", "arete", "reports.json")


def report_current_card():
    """Report an issue with the current reviewing card."""
    reviewer = mw.reviewer
    if not reviewer or not reviewer.card:
        showWarning("No card is currently being reviewed.")
        return

    card = reviewer.card
    note = card.note()
    source = get_obsidian_source(note)

    if not source:
        showWarning(
            "No Obsidian source found for this card.\n\n"
            "Make sure the card was synced with arete and has the "
            "'_obsidian_source' field."
        )
        return

    issue_note, ok = QInputDialog.getText(mw, "Report Card Issue", "What's the issue?")
    if not ok or not issue_note.strip():
        return

    vault, file_path, card_idx, arete_id = source

    # Get front text from the note's first field, stripped of HTML
    fields = note.fields
    front_text = re.sub(r"<[^>]*>", "", fields[0]).strip() if fields else ""
    if len(front_text) > 80:
        front_text = front_text[:77] + "..."

    report = {
        "nid": note.id,
        "cid": card.id,
        "arete_id": arete_id,
        "file_path": file_path,
        "line": card_idx,
        "front": front_text,
        "note": issue_note.strip(),
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }

    # Append to reports file
    os.makedirs(os.path.dirname(REPORTS_PATH), exist_ok=True)
    reports = []
    if os.path.exists(REPORTS_PATH):
        try:
            with open(REPORTS_PATH, encoding="utf-8") as f:
                reports = json.load(f)
        except (json.JSONDecodeError, OSError):
            reports = []
    reports.append(report)
    with open(REPORTS_PATH, "w", encoding="utf-8") as f:
        json.dump(reports, f, indent=2, ensure_ascii=False)

    # Suspend the card so it doesn't come up again until fixed
    card = reviewer.card
    mw.col.sched.suspend_cards([card.id])

    tooltip(f"Reported & suspended: {issue_note.strip()}")

    # Move to the next card
    mw.reviewer.nextCard()


def setup_reviewer_shortcut():
    """Add keyboard shortcuts and menu items."""
    # Open in Obsidian (Ctrl+Shift+O = Cmd+Shift+O on Mac)
    action = QAction("Open in Obsidian", mw)
    action.setShortcut(QKeySequence("Ctrl+Shift+O"))
    action.triggered.connect(open_current_card_in_obsidian)
    mw.form.menuTools.addAction(action)

    # Copy source path (Ctrl+C = Cmd+C on Mac, only active during review)
    copy_action = QAction("Copy Obsidian Source", mw)
    copy_action.setShortcut(QKeySequence("Ctrl+C"))
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
    selected = browser.selectedNotes()
    if not selected:
        return

    action = menu.addAction("Open in Obsidian")
    action.triggered.connect(lambda: open_selected_notes_in_obsidian(browser))


def open_selected_notes_in_obsidian(browser: Browser):
    """Open selected notes in Obsidian (first one if multiple selected)."""
    selected = browser.selectedNotes()
    if not selected:
        showWarning("No notes selected.")
        return

    # Open first selected note
    note_id = selected[0]
    note = mw.col.get_note(note_id)

    source = get_obsidian_source(note)
    if not source:
        showWarning(
            "No Obsidian source found for this note.\n\n"
            "Make sure the note was synced with arete and has the "
            "'_obsidian_source' field."
        )
        return

    vault, file_path, card_idx, _arete_id = source
    if open_obsidian_uri(vault, file_path, card_idx):
        tooltip(f"Opening in Obsidian: {file_path}")

    # If multiple selected, notify user
    if len(selected) > 1:
        tooltip(f"Opened first of {len(selected)} selected notes")
