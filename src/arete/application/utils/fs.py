import contextlib
import errno
import hashlib
import os
import stat
import sys
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path

# Windows refuses to replace a file another program holds open (an editor, a virus scanner,
# a sync client), where POSIX replaces it regardless. Such a hold is usually brief, so the
# replace is tried again after each pause here, then refused with a message naming the note.
OPEN_FILE_PAUSES = (0.05, 0.1, 0.2, 0.4, 0.8) if sys.platform == "win32" else ()


def _replace(tmp: str, target: Path) -> None:
    for pause in (*OPEN_FILE_PAUSES, None):
        try:
            os.replace(tmp, target)
            return
        except PermissionError as e:
            if not OPEN_FILE_PAUSES:
                raise
            if pause is None:
                raise PermissionError(
                    e.errno,
                    f"another program has {target.name} open, so it cannot be replaced; "
                    "close it or wait, then sync again",
                    target.as_posix(),
                ) from e
            time.sleep(pause)


def replace_file(path: Path, data: bytes) -> None:
    """Write `data` to `path` so that a crash or a kill leaves the old file or the new one.

    Every write of a vault note goes through here. The bytes go to a hidden temp file in
    the note's folder, flushed and fsynced, which then replaces the note in one step
    (`os.replace`). The note's permission bits are kept; a symlinked note is written at
    its target. A note that may not be written (read-only) is refused, as opening it for
    writing would be: replacing it would succeed in a writable folder. A note another
    program holds open on Windows is tried again briefly (`OPEN_FILE_PAUSES`), then refused
    with a PermissionError naming it. On any failure the temp file is removed and the note
    is as it was.
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
        _replace(tmp, target)
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


def read_text_and_newline(path: Path) -> tuple[str, str]:
    """A note's UTF-8 text with LF line endings, and the line ending it is stored with.

    A note with any CRLF counts as CRLF. Pass both to `replace_text` to write the note
    back with its own line endings, on every OS.
    """
    raw = path.read_bytes().decode("utf-8")
    return raw.replace("\r\n", "\n"), "\r\n" if "\r\n" in raw else "\n"


def replace_text(path: Path, text: str, newline: str = "\n") -> None:
    """`replace_file` for LF text, stored as UTF-8 with `newline` as its line ending."""
    replace_file(path, text.replace("\n", newline).encode("utf-8"))


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
