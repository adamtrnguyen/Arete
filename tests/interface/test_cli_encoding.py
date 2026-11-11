"""The CLI prints UTF-8 whatever the stream would encode, so Windows pipes cannot break it.

On Windows a pipe encodes with the ANSI code page (cp1252), which has no check mark, no
arrow and no byte order mark; the Obsidian plugin reads every command through a pipe and
decodes UTF-8. PYTHONIOENCODING=cp1252 gives a child process that pipe on any OS.
"""

import io
import os
import subprocess
import sys

import pytest

from arete.interface._common import utf8_console

CLEAN = "---\narete: true\ndeck: Default\ncards:\n- Front: Cramér\n  Back: B\n---\nbody\n"


def test_a_cp1252_stream_is_switched_to_utf8(monkeypatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)

    utf8_console()
    print("obsidian → anki ✅ ﻿")
    stream.flush()

    assert raw.getvalue().decode("utf-8") == "obsidian → anki ✅ ﻿\n".replace("\n", os.linesep)


def test_a_utf8_stream_is_left_as_it_is(monkeypatch):
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    utf8_console()
    assert (sys.stdout.encoding, sys.stdout.errors) == ("utf-8", "strict")


@pytest.mark.parametrize("args", [["vault", "fix"], ["vault", "check"]])
def test_a_command_piped_through_cp1252_prints_utf8(tmp_path, args):
    note = tmp_path / "Cramér.md"
    note.write_text(CLEAN, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env["PYTHONIOENCODING"] = "cp1252"

    done = subprocess.run(
        [sys.executable, "-m", "arete", *args, str(note)],
        capture_output=True,
        env=env,
        timeout=120,
    )

    out = done.stdout.decode("utf-8")
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert "✅" in out
