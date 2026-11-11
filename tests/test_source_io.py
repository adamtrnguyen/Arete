"""Every text file the code opens names its encoding: the platform default is not UTF-8.

On Windows `open()`, `Path.read_text()` and `Path.write_text()` without `encoding=` use the
ANSI code page (cp1252 on most machines), so a note with a byte order mark, an accent or an
arrow could not be written or was read as mojibake. Ruff's rule for this (PLW1514) is a
preview rule and only sees receivers it can type, so this test reads the syntax instead:
a binary mode (`"rb"`, `"wb"`) needs no encoding; `encoding: ok <reason>` on the line exempts
one call.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHECKED = (ROOT / "src", ROOT / "scripts", ROOT / "arete_ankiconnect")
EXEMPT = "encoding: ok"
TEXT_IO = {"read_text", "write_text"}
MODE_LETTERS = set("rwxabt+")


def _mode(call: ast.Call, position: int) -> str | None:
    """The literal mode argument of an open call, positional or keyword, if there is one."""
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    if len(call.args) > position and isinstance(call.args[position], ast.Constant):
        return str(call.args[position].value)  # type: ignore[union-attr]
    return None


def _needs_encoding(call: ast.Call) -> bool:
    if any(kw.arg == "encoding" for kw in call.keywords):
        return False
    func = call.func
    if isinstance(func, ast.Name) and func.id == "open":  # builtin open(file, mode)
        return "b" not in (_mode(call, 1) or "r")
    if isinstance(func, ast.Attribute) and func.attr in TEXT_IO:
        return True
    if isinstance(func, ast.Attribute) and func.attr == "open":  # Path.open(mode)
        mode = _mode(call, 0)  # aqt.dialogs.open("Browser") is not a file: no mode letters
        return mode is not None and set(mode) <= MODE_LETTERS and "b" not in mode
    return False


def unencoded_calls(root: pathlib.Path, base: pathlib.Path = ROOT) -> list[str]:
    found = []
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and _needs_encoding(node):
                if EXEMPT not in lines[node.lineno - 1]:
                    found.append(f"{path.relative_to(base).as_posix()}:{node.lineno}")
    return found


def test_every_text_file_opened_names_its_encoding():
    found = [hit for root in CHECKED for hit in unencoded_calls(root)]
    assert found == [], f"add encoding='utf-8' (or a binary mode): {found}"


def test_the_check_sees_each_form(tmp_path):
    (tmp_path / "m.py").write_text(
        "open(p)\nopen(p, 'w')\nopen(p, 'rb')\nopen(p, encoding='utf-8')\n"
        "p.read_text()\np.write_text(s, encoding='utf-8')\np.open('w')\np.open('rb')\n"
        "webbrowser.open(uri)\naqt.dialogs.open('Browser', w)\nopen(p)  # encoding: ok a test\n",
        encoding="utf-8",
    )
    assert unencoded_calls(tmp_path, tmp_path) == ["m.py:1", "m.py:2", "m.py:5", "m.py:7"]
