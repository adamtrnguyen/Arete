import copy
import hashlib
import logging
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from arete.application.sync.id_writeback import (
    CardIds,
    generate_arete_id,
    read_exact,
    write_card_ids,
    write_exact,
)
from arete.application.utils.common import sanitize
from arete.application.utils.fs import iter_markdown_files, read_text_and_newline, replace_text
from arete.application.utils.text import (
    frontmatter_slice,
    parse_frontmatter,
    rebuild_markdown_with_frontmatter,
)
from arete.domain.interfaces import ContentCache
from arete.domain.models import UpdateItem

# _quick_check_file reasons for an Arete file whose cards (and nids) could not be read.
UNREADABLE_REASONS = ("stat_error", "read_error", "no_or_bad_yaml")

# Stat-cache meta for a file whose text is not an Arete note. The entry matches only the
# mtime and size it was read at, so any edit (one that adds `arete:` included) misses it
# and the file is read again. A reader that knows only `arete: true` entries sees a miss.
NOT_ARETE_META: dict[str, Any] = {"__not_arete__": True}


class VaultService:
    def __init__(self, root: Path, cache: ContentCache, ignore_cache: bool = False):
        """Initialize VaultService."""
        self.root = root
        self.cache = cache
        self.ignore_cache = ignore_cache
        self.logger = logging.getLogger(__name__)
        # Arete files the last scan could not read. Their note ids are unknowable, so
        # nothing in Anki can be proven orphaned while this is non-empty (see prune).
        self.unreadable: list[tuple[Path, str]] = []
        # Plain notes read by this scan: (path, mtime, size) at the stat before the read.
        self._not_arete: list[tuple[Path, float, int]] = []
        # This run's frontmatter parses by path: (text, untouched meta, body).
        self._parsed: dict[Path, tuple[str, dict[str, Any], str]] = {}
        # The last apply_updates' cards whose ids could not be written: (file, index, why).
        self.write_refused: list[tuple[Path, int, str]] = []

    def parse(self, md_path: Path, text: str) -> tuple[dict[str, Any], str]:
        """parse_frontmatter(text), reusing this run's parse of the same text of this file.

        The caller owns the meta it gets and may change it: a reused parse is a deep copy.
        Every stage still reads the file itself, so an edit made during the sync is seen.
        The text is parsed with LF line endings, so the scan (`read_text`, which turns CRLF
        into LF) and the id write-back (`read_exact`, which keeps it) share one parse of a
        CRLF note; the meta is the same either way, and the body comes back with LF.
        """
        text = text.replace("\r\n", "\n")
        hit = self._parsed.get(md_path)
        if hit is not None and hit[0] == text:
            return copy.deepcopy(hit[1]), hit[2]
        meta, body = parse_frontmatter(text)
        self._parsed[md_path] = (text, copy.deepcopy(meta), body)
        return meta, body

    def scan_for_compatible_files(self) -> Iterable[tuple[Path, dict[str, Any], bool]]:
        """Iterate over all markdown files in the vault, check them for validity.

        A file counts when its frontmatter has `arete: true` and a non-empty `cards` list.
        Return: (path, meta, is_fresh)
                 is_fresh=True means we just parsed it (cache was cold/dirty).
                 is_fresh=False means we loaded meta from stat-cache (cache was warm).
        """
        self.unreadable = []
        self._not_arete = []
        self._parsed = {}
        for p in iter_markdown_files(self.root):
            ok, _, reason, meta, is_fresh = self._quick_check_file(p)
            if not ok and reason and reason.startswith(UNREADABLE_REASONS):
                self.unreadable.append((p, reason))
            if ok and meta:
                cards_count = len(meta.get("cards", []))
                self.logger.debug(f"[vault] Accepted {p.name} cards={cards_count} fresh={is_fresh}")
                yield p, meta, is_fresh
            else:
                self.logger.debug(f"[vault] Skipped {p.name}: {reason}")
        self._remember_not_arete()

    def _remember_not_arete(self) -> None:
        """Store this scan's plain notes in the stat cache, in one transaction."""
        rows = [(p, "", NOT_ARETE_META, mtime, size) for p, mtime, size in self._not_arete]
        self._not_arete = []
        if not rows or not self.cache:
            return
        try:
            self.cache.set_file_metas(rows)
        except Exception as e:
            self.logger.warning(f"[cache] could not record {len(rows)} plain notes: {e}")

    def _card_position(self, cards: list[Any], u: UpdateItem) -> int | None:
        """Where to write this card's nid/cid: found by its Arete id, not its position.

        A card inserted or moved while a sync ran shifts every index after it, and writing
        by position then stamped one card's nid onto another card.
        """
        arete_id = u.note.arete_id if u.note else None
        if arete_id:
            for i, c in enumerate(cards):
                if isinstance(c, dict) and sanitize(c.get("id", "")).strip() == arete_id:
                    return i
            self.logger.warning(
                f"[write] {u.source_file.name}: card {arete_id} moved or vanished during "
                f"sync; not writing nid {u.new_nid} (the next sync reconciles it)"
            )
            return None
        i = u.source_index - 1  # a card without an id: position is all there is
        return i if 0 <= i < len(cards) else None

    def _lookup_stat_cache(
        self, md_file: Path, mtime: float, size: int
    ) -> tuple[bool, int, str | None, dict[str, Any] | None, bool] | None:
        """Return a cached quick-check result for unchanged `(mtime, size)`, or None on miss."""
        if not self.cache or self.ignore_cache:
            return None
        try:
            cached_meta = self.cache.get_file_meta_by_stat(md_file, mtime, size)
            if cached_meta == NOT_ARETE_META:
                return (False, 0, "not_arete_file", None, False)
            if cached_meta and cached_meta.get("arete") is True:
                cards = cached_meta.get("cards", [])
                return (True, len(cards), None, cached_meta, False)
        except Exception as e:
            self.logger.warning(f"[vault] cache miss (error) for {md_file.name}: {e}")
        return None

    @staticmethod
    def _frontmatter_skip_reason(text: str) -> str | None:
        """Screen frontmatter text before full YAML parsing; return reason or None to parse."""
        # Inspect frontmatter only (never prose body) so a plain note mentioning `arete:`
        # in body text is never misclassified as broken YAML.
        fm = frontmatter_slice(text)
        if fm == "":
            return "not_arete_file"
        if fm is None:
            return "no_or_bad_yaml" if "arete" in text else "not_arete_file"
        if "arete" not in fm and "\\" not in fm:
            return "not_arete_file"
        return None

    def _quick_check_file(
        self, md_file: Path
    ) -> tuple[bool, int, str | None, dict[str, Any] | None, bool]:
        # Returns: (ok, num_cards, reason, meta, is_fresh)
        try:
            st = md_file.stat()
            mtime, size = st.st_mtime, st.st_size
        except Exception as e:
            return (False, 0, f"stat_error:{e}", None, True)

        if (cached := self._lookup_stat_cache(md_file, mtime, size)) is not None:
            return cached

        try:
            text = md_file.read_text(encoding="utf-8", errors="strict")
        except Exception as e:
            return (False, 0, f"read_error:{e}", None, True)

        if reason := self._frontmatter_skip_reason(text):
            if reason == "not_arete_file":
                self._not_arete.append((md_file, mtime, size))
            return (False, 0, reason, None, True)

        meta, _body = self.parse(md_file, text)
        if "__yaml_error__" in meta:
            return (False, 0, "no_or_bad_yaml", None, True)
        if not meta or meta.get("arete") is not True:
            self._not_arete.append((md_file, mtime, size))
            return (False, 0, "not_arete_file", None, True)

        cards = meta.get("cards", [])
        if not isinstance(cards, list) or not cards:
            return (False, 0, "no_cards", None, True)

        # Deckless `arete: true` notes fall back consistently to `default_deck` in MarkdownParser
        # across normal, `--force`, and warm-cache runs.
        # An Arete file enters the stat cache in record_synced, after its cards reached
        # Anki. Writing it here let a dry run, or a run where a card failed, hide the edit
        # from every later sync. Only plain notes are remembered by the scan itself.
        return (True, len(cards), None, meta, True)

    def record_synced(self, paths: Iterable[Path]) -> None:
        """Mark files as synced in the stat cache, so the next run can skip them.

        Call only for files whose every card reached Anki, and never on a dry run.
        Reads the file as it is now, after any nid/cid write-back.
        """
        if not self.cache:
            return
        for md_path in paths:
            try:
                st = md_path.stat()
                text = md_path.read_text(encoding="utf-8")
                meta, _ = self.parse(md_path, text)
                if not meta or "__yaml_error__" in meta:
                    continue
                st_after = md_path.stat()
                if (st_after.st_mtime, st_after.st_size) != (st.st_mtime, st.st_size):
                    continue
                file_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
                self.cache.set_file_meta(
                    md_path, file_hash, meta, mtime=st.st_mtime, size=st.st_size
                )
            except Exception as e:
                self.logger.warning(f"[cache] could not record {md_path.name} as synced: {e}")

    def format_vault(self, dry_run: bool = False) -> int:
        """Scan and re-serialize all compatible files to normalize YAML.

        A file keeps its line endings (LF or CRLF), whatever the OS.
        Return the number of files updated.
        """
        count = 0
        for md_path, _cached_meta, _is_fresh in self.scan_for_compatible_files():
            try:
                text, newline = read_text_and_newline(md_path)
                fresh_meta, body = self.parse(md_path, text)
                if not fresh_meta or "__yaml_error__" in fresh_meta:
                    continue

                # Rebuild using our dumper (which now uses |-)
                new_text = rebuild_markdown_with_frontmatter(fresh_meta, body)

                if new_text != text:
                    if dry_run:
                        self.logger.info(f"[dry-run] Would format {md_path.name}")
                    else:
                        replace_text(md_path, new_text, newline)
                        self.logger.debug(f"[format] {md_path.name}: normalized YAML")
                    count += 1
            except Exception as e:
                self.logger.error(f"[error] formatting {md_path.name}: {e}")

        return count

    def card_lines(self, md_path: Path) -> dict[int, int]:
        """Each card's start line in the file as it is now, by card index (1-based)."""
        try:
            meta, _ = self.parse(md_path, md_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.logger.warning(f"[vault] could not read card lines of {md_path.name}: {e}")
            return {}
        if not meta or "__yaml_error__" in meta:
            return {}
        return {
            idx: int(card["__line__"])
            for idx, card in enumerate(meta.get("cards") or [], start=1)
            if isinstance(card, dict) and "__line__" in card
        }

    def assign_ids(
        self, md_path: Path, dry_run: bool = False
    ) -> tuple[int, dict[str, Any], dict[int, str]]:
        """Give each card without an `id` a new Arete id, one inserted line per card.

        Returns (ids assigned, the file's meta with them, refused cards by 0-based index).
        A refusal writes nothing and assigns nothing; a dry run writes nothing and returns
        the meta the write would give. Raises when the file cannot be read or written.
        """
        text = read_exact(md_path)
        meta, _ = self.parse(md_path, text)
        cards = meta.get("cards")
        edits = [
            CardIds(i, id=generate_arete_id())
            for i, card in enumerate(cards if isinstance(cards, list) else [])
            if isinstance(card, dict) and not sanitize(card.get("id", "")).strip()
        ]
        result = write_card_ids(text, edits)
        if result.refused or result.text == text:
            return 0, meta, result.refused
        if not dry_run:
            write_exact(md_path, result.text)
        new_meta, _ = self.parse(md_path, result.text)
        return len(edits), new_meta, {}

    def _apply_file_updates(self, md_path: Path, ups: list[UpdateItem], *, dry_run: bool) -> bool:
        """Apply nid/cid write-back to a single file; return True when written to disk."""
        text = read_exact(md_path)
        meta, _ = self.parse(md_path, text)
        if not meta or "__yaml_error__" in meta:
            return False
        cards = meta.get("cards", [])
        edits = [
            (u, CardIds(i, nid=u.new_nid, cid=u.new_cid))
            for u in ups
            if (i := self._card_position(cards, u)) is not None
        ]
        result = write_card_ids(text, [e for _, e in edits])
        if result.refused:
            for u, e in edits:
                if e.index in result.refused:
                    why = result.refused[e.index]
                    self.write_refused.append((md_path, u.source_index, why))
                    self.logger.error(
                        f"[write] {md_path.name} #{u.source_index}: not writing its "
                        f"Anki ids, {why}; the file is unchanged"
                    )
            return False
        if result.text == text:
            return False
        if dry_run:
            self.logger.info(f"[dry-run] Would write nid/cid into {md_path}")
            return False
        write_exact(md_path, result.text)
        self.logger.debug(f"[write] {md_path}: persisted nid/cid into frontmatter")
        return True

    def apply_updates(self, updates: list[UpdateItem], dry_run: bool = False) -> set[Path]:
        """Write new nids/cids into the cards' frontmatter; the files written.

        Only the id lines change (`id_writeback`). A card whose ids cannot be written that
        way, or whose file cannot be read or replaced, is listed in `write_refused` as
        (file, card index, reason), and its file is left as it was.
        """
        written: set[Path] = set()
        self.write_refused = []
        by_file: dict[Path, list[UpdateItem]] = defaultdict(list)
        for u in updates:
            if u.ok and (u.new_nid or u.new_cid):
                by_file[u.source_file].append(u)

        for md_path, ups in by_file.items():
            try:
                if self._apply_file_updates(md_path, ups, dry_run=dry_run):
                    written.add(md_path)
            except Exception as e:
                self.logger.error(f"[error] write-updates {md_path.as_posix()}: {e}")
                self.write_refused += [
                    (md_path, u.source_index, f"the write failed: {e}") for u in ups
                ]
        return written
