import logging
import os
import re
import shutil
from pathlib import Path
from urllib.parse import quote, unquote

from arete.application.utils.consts import MARKDOWN_IMG_RE, WIKILINK_IMG_RE, WIKILINK_RE
from arete.application.utils.fs import file_md5
from arete.domain.constants import MEDIA_DIR_NAMES


def unique_media_name(dest_dir: Path, src: Path) -> str:
    base = src.name
    cand = dest_dir / base
    if not cand.exists():
        return base
    try:
        if cand.stat().st_size == src.stat().st_size:
            if file_md5(cand) == file_md5(src):
                return base
    except Exception:
        pass
    stem, suf = src.stem, src.suffix
    short = file_md5(src)[:8]
    return f"{stem}_{short}{suf}"


def _copy_to_anki_media(src: Path, anki_media_dir: Path, logger: logging.Logger) -> str | None:
    try:
        anki_media_dir.mkdir(parents=True, exist_ok=True)
        media_name = unique_media_name(anki_media_dir, src)
        shutil.copy2(src, anki_media_dir / media_name)
        logger.debug(f"[media] copied {src} → {anki_media_dir / media_name}")
        return media_name
    except Exception as e:
        logger.warning(f"[media] failed to copy '{src}': {e}")
        return None


def build_filename_index(vault_root: Path, logger: logging.Logger) -> dict[str, list[Path]]:
    idx: dict[str, list[Path]] = {}
    for root in MEDIA_DIR_NAMES:
        base = vault_root / root
        if base.is_dir():
            for dirpath, _, filenames in os.walk(base):
                for f in filenames:
                    p = Path(dirpath) / f
                    if p.is_file():
                        idx.setdefault(p.name, []).append(p.resolve())

    logger.debug(f"[media-index] entries={len(idx)}")
    return idx


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
    """Convert ``[[Target]]`` and ``[[Target|display]]`` wikilinks to
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
