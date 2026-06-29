import hashlib
from collections.abc import Iterable
from pathlib import Path


def iter_markdown_files(root: Path) -> Iterable[Path]:
    """Recursively find markdown files in a vault, skipping common hidden.

    and system directories.
    """
    if root.is_file() and root.suffix.lower() == ".md":
        yield root
        return
    skip_dirs = {".git", ".obsidian", ".trash", ".venv", "node_modules"}
    for p in root.rglob("*.md"):
        try:
            rel = p.relative_to(root)
        except ValueError:
            # p isn't under root (shouldn't happen for rglob results). Fail open:
            # yield the markdown file rather than silently dropping it; we just
            # can't apply the skip-dir filter to a path we can't make relative.
            yield p
            continue
        rel_parts = set(rel.parts[:-1])
        if rel_parts & skip_dirs:
            continue
        if any(part.startswith(".") for part in rel_parts):
            continue
        yield p


def file_md5(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
