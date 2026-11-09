import logging
import os
import re
import shutil
from pathlib import Path
from urllib.parse import quote, unquote

from arete.application.utils.consts import MARKDOWN_IMG_RE, WIKILINK_IMG_RE, WIKILINK_RE
from arete.application.utils.fs import file_md5
from arete.domain.constants import MEDIA_DIR_NAMES


def _media_target(dest_dir: Path, src: Path) -> tuple[str, bool]:
    """The name `src` gets in `dest_dir`, and whether that file already holds its bytes."""
    base = src.name
    cand = dest_dir / base
    if not cand.exists():
        return base, False
    try:
        if cand.stat().st_size == src.stat().st_size:
            if file_md5(cand) == file_md5(src):
                return base, True
    except Exception as e:
        # Cannot compare -> fall through to a uniquified name (a duplicate copy, not data loss).
        logging.getLogger(__name__).debug(f"[media] could not compare {cand} with {src}: {e}")
    stem, suf = src.stem, src.suffix
    short = file_md5(src)[:8]
    return f"{stem}_{short}{suf}", False


def unique_media_name(dest_dir: Path, src: Path) -> str:
    return _media_target(dest_dir, src)[0]


def _copy_to_anki_media(src: Path, anki_media_dir: Path, logger: logging.Logger) -> str | None:
    try:
        anki_media_dir.mkdir(parents=True, exist_ok=True)
        media_name, present = _media_target(anki_media_dir, src)
        dest = anki_media_dir / media_name
        # An identical file is left alone. A source that IS the destination still goes to
        # copy2, which refuses it, so that embed keeps resolving as it always has.
        if present and not os.path.samefile(src, dest):
            logger.debug(f"[media] {dest} already holds {src}")
            return media_name
        shutil.copy2(src, dest)
        logger.debug(f"[media] copied {src} → {dest}")
        return media_name
    except Exception as e:
        logger.warning(f"[media] failed to copy '{src}': {e}")
        return None


def _index_dir(top: str, idx: dict[str, list[Path]]) -> None:
    """Add the files under `top` to `idx` in os.walk's top-down order, as resolved paths.

    A regular file's resolved path is its resolved directory plus its name, so only a
    symlink needs its own resolve() and is_file(). Symlinked directories are not entered.
    """
    try:
        with os.scandir(top) as it:
            entries = list(it)
    except OSError:
        return
    real_dir: list[Path] = []  # `top` resolved, once, on its first regular file
    subdirs: list[str] = []
    for entry in entries:
        if _is_dir(entry):
            if not entry.is_symlink():
                subdirs.append(entry.path)
            continue
        path = _resolved_file(entry, top, real_dir)
        if path is not None:
            idx.setdefault(entry.name, []).append(path)
    for sub in subdirs:
        _index_dir(sub, idx)


def _is_dir(entry: os.DirEntry) -> bool:
    try:
        return entry.is_dir()
    except OSError:
        return False


def _resolved_file(entry: os.DirEntry, top: str, real_dir: list[Path]) -> Path | None:
    try:
        if entry.is_symlink():
            p = Path(entry.path)
            return p.resolve() if p.is_file() else None
        if not entry.is_file(follow_symlinks=False):
            return None
    except OSError:
        return None
    if not real_dir:
        real_dir.append(Path(top).resolve())
    return real_dir[0] / entry.name


def build_filename_index(vault_root: Path, logger: logging.Logger) -> dict[str, list[Path]]:
    idx: dict[str, list[Path]] = {}
    for root in MEDIA_DIR_NAMES:
        base = vault_root / root
        if base.is_dir():
            _index_dir(str(base), idx)

    logger.debug(f"[media-index] entries={len(idx)}")
    return idx


class FilenameIndex(dict[str, list[Path]]):
    """The vault's attachment index, walked on the first image lookup that needs it.

    A sync whose cards embed no image never walks the attachment folders.
    """

    def __init__(self, vault_root: Path, logger: logging.Logger):
        """Hold what the walk needs; the walk itself waits for `ensure_built`."""
        super().__init__()
        self._vault_root = vault_root
        self._logger = logger
        self._built = False

    def ensure_built(self) -> None:
        if not self._built:
            self._built = True
            self.update(build_filename_index(self._vault_root, self._logger))


def _resolve_candidate_paths(
    md_path: Path, vault_root: Path, raw: str, name_index: dict[str, list[Path]] | None = None
) -> list[Path]:
    target = unquote(raw).strip()
    if "|" in target:
        target = target.split("|", 1)[0].strip()
    p = Path(target)

    candidates: list[Path] = [
        (md_path.parent / p).resolve(),
        (vault_root / p).resolve(),
    ]

    filename = p.name
    if isinstance(name_index, FilenameIndex) and filename:
        name_index.ensure_built()
    if name_index and filename:
        candidates.extend(name_index.get(filename, []))

    seen, uniq = set(), []
    for c in candidates:
        if c not in seen:
            uniq.append(c)
            seen.add(c)
    return uniq


def transform_images_in_text(
    text: str,
    md_path: Path,
    vault_root: Path,
    anki_media_dir: Path,
    logger: logging.Logger,
    name_index: dict[str, list[Path]] | None = None,
) -> str:
    def repl_wikilink(m: re.Match) -> str:
        raw = m.group(1)
        candidates = _resolve_candidate_paths(md_path, vault_root, raw, name_index=name_index)

        for cand in candidates:
            if cand.exists() and cand.is_file():
                media_name = _copy_to_anki_media(cand, anki_media_dir, logger)
                if media_name:
                    return f"![]({media_name})"
                else:
                    logger.warning(f"[media] Copy failed for '{cand}'")

        logger.warning(f"[media] missing file for embed '{raw}' in {md_path}")
        return m.group(0)

    out = WIKILINK_IMG_RE.sub(repl_wikilink, text)

    def repl_markdown_img(m: re.Match) -> str:
        raw = m.group(1).strip()
        if re.match(r"^[a-z]+://", raw, flags=re.I):
            return m.group(0)
        for cand in _resolve_candidate_paths(md_path, vault_root, raw, name_index=name_index):
            if cand.exists() and cand.is_file():
                media_name = _copy_to_anki_media(cand, anki_media_dir, logger)
                return m.group(0).replace(raw, media_name) if media_name else m.group(0)
        return m.group(0)

    out = MARKDOWN_IMG_RE.sub(repl_markdown_img, out)
    return out


def transform_wikilinks_to_uri(text: str, vault_name: str) -> str:
    """Convert ``[[Target]]`` and ``[[Target|display]]`` wikilinks to.

    clickable ``obsidian://open`` URIs.  The resulting markdown link is
    later rendered to an ``<a>`` tag by the markdown→HTML converter.
    """

    def _repl(m: re.Match) -> str:
        raw = m.group(1)
        if "|" in raw:
            target, display = raw.split("|", 1)
        else:
            target = raw
            display = raw
        target = target.strip()
        display = display.strip()
        uri = f"obsidian://open?vault={quote(vault_name)}&file={quote(target)}"
        return f"[{display}]({uri})"

    return WIKILINK_RE.sub(_repl, text)
