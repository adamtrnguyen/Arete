"""The Anki profile seeded into the integration container keeps Anki's update check off."""

import pickle
import sqlite3
from pathlib import Path

PREFS = Path(__file__).parent / "fixtures" / "anki" / "prefs21.db"


def _row(name: str) -> dict:
    con = sqlite3.connect(f"file:{PREFS}?mode=ro", uri=True)
    try:
        (data,) = con.execute("select data from profiles where name=?", (name,)).fetchone()
    finally:
        con.close()
    return pickle.loads(data)  # noqa: S301 - a fixture committed to this repo


def test_the_global_profile_turns_the_update_check_off():
    # aqt.profiles.ProfileManager.check_for_updates reads this key from the _global row.
    assert _row("_global")["check_for_updates"] is False


def test_the_first_run_wizard_stays_skipped():
    meta = _row("_global")
    assert meta["firstRun"] is False
    assert meta["last_loaded_profile_name"] == "User 1"
