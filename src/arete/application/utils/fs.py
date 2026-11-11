import contextlib
import errno
import hashlib
import os
import stat
import tempfile
from collections.abc import Iterable
from pathlib import Path


def replace_file(path: Path, data: bytes) -> None:
    """Write `data` to `path` so that a crash or a kill leaves the old file or the new one.

    Every write of a vault note goes through here. The bytes go to a hidden temp file in
    the note's folder, flushed and fsynced, which then replaces the note in one step
    (`os.replace`). The note's permission bits are kept; a symlinked note is written at
    its target. A note that may not be written (read-only) is refused, as opening it for
    writing would be: replacing it would succeed in a writable folder. On any failure the
    temp file is removed and the note is as it was.
    """
    target = Path(os.path.realpath(path))
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
        if not os.access(target, os.W_OK):
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    if os.name == "posix":  # the rename itself survives a power cut once the folder is synced
        dir_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def replace_text(path: Path, text: str) -> None:
    """`replace_file` for text, with newlines written as `Path.write_text` writes them."""
    replace_file(path, text.replace("\n", os.linesep).encode("utf-8"))


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
