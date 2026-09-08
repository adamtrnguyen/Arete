"""Regenerate the package tree and layer table inside docs/ARCHITECTURE.md.

The hand-written tree in that file had drifted badly: it named `domain/types.py`,
`application/pipeline.py`, `application/parser.py`, `interface/server.py` and an
"Apy" adapter, none of which exist. A reader following it looked for files that
were not there.

Everything between the GENERATED markers is derived from the package, so it cannot
drift. The prose outside the markers stays hand-written.

    uv run --no-sync python scripts/gen_architecture.py          # rewrite
    uv run --no-sync python scripts/gen_architecture.py --check  # fail on drift
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "arete"
DOC = ROOT / "docs" / "ARCHITECTURE.md"
BEGIN = "<!-- BEGIN GENERATED: package-tree (scripts/gen_architecture.py) -->"
END = "<!-- END GENERATED -->"

LAYERS = {
    "interface": "How a person or a client reaches the system: CLI, HTTP, MCP.",
    "composition": "The only place that picks an adapter for a port and wires a use-case.",
    "application": "Use-cases. Decides what happens next, over ports, never over adapters.",
    "infrastructure": "One technology each: AnkiConnect over HTTP, the Anki library, SQLite.",
    "domain": "Types and ports. True whatever the technology is.",
}


def summarize(path: Path) -> str:
    """First line of the module docstring, or the public names it defines."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return ""
    doc = ast.get_docstring(tree)
    if doc:
        return doc.strip().splitlines()[0].rstrip(".")
    names = [
        n.name
        for n in tree.body
        if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and not n.name.startswith("_")
    ]
    return ", ".join(names[:3]) + ("..." if len(names) > 3 else "")


def _walk(base: Path, depth: int, out: list[str]) -> None:
    """Emit sub-packages then modules, so a reader sees the directory a file lives in."""
    pad = "│   " * depth
    for sub in sorted(d for d in base.iterdir() if d.is_dir() and d.name != "__pycache__"):
        mods = [p for p in sub.rglob("*.py") if p.name != "__init__.py"]
        if not mods:
            continue
        out.append(f"│   {pad}├── {sub.name + '/':<{max(6, 26 - 4 * depth)}}")
        _walk(sub, depth + 1, out)
    width = max(6, 26 - 4 * depth)
    for module in sorted(p for p in base.glob("*.py") if p.name != "__init__.py"):
        out.append(f"│   {pad}├── {module.name:<{width}} {summarize(module)}".rstrip())


def tree_lines() -> list[str]:
    out: list[str] = ["src/arete/"]
    for layer, purpose in LAYERS.items():
        base = PKG / layer
        if not base.is_dir():
            continue
        out.append("│")
        out.append(f"├── {layer + '/':<26} {purpose}")
        _walk(base, 0, out)
    mods = sorted(p for p in PKG.glob("*.py") if p.name != "__init__.py")
    if mods:
        out.append("│")
        for module in mods:
            out.append(f"├── {module.name:<26} {summarize(module)}".rstrip())
    return out


def counts_table() -> list[str]:
    rows = ["| Layer | Modules | Lines | May import |", "|---|---|---|---|"]
    allowed = {
        "interface": "application, composition",
        "composition": "application, infrastructure, domain",
        "application": "domain (ports only)",
        "infrastructure": "domain",
        "domain": "nothing in arete",
    }
    for layer in LAYERS:
        base = PKG / layer
        if not base.is_dir():
            continue
        mods = [p for p in base.rglob("*.py") if p.name != "__init__.py"]
        lines = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in mods)
        rows.append(f"| `{layer}` | {len(mods)} | {lines} | {allowed[layer]} |")
    return rows


def render() -> str:
    body = [
        BEGIN,
        "",
        "```text",
        *tree_lines(),
        "```",
        "",
        *counts_table(),
        "",
        "The import rules in the last column are enforced by `just check-architecture`",
        "(import-linter), not by convention.",
        "",
        END,
    ]
    return "\n".join(body)


def main() -> int:
    text = DOC.read_text(encoding="utf-8")
    start, stop = text.find(BEGIN), text.find(END)
    if start == -1 or stop == -1:
        print(f"{DOC}: missing the GENERATED markers", file=sys.stderr)
        return 2
    updated = text[:start] + render() + text[stop + len(END) :]
    if "--check" in sys.argv:
        if updated != text:
            print(
                f"{DOC} is out of date. Run: uv run --no-sync python scripts/gen_architecture.py",
                file=sys.stderr,
            )
            return 1
        print(f"{DOC} is current")
        return 0
    DOC.write_text(updated, encoding="utf-8")
    print(f"wrote {DOC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
