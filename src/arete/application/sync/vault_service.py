import hashlib
import logging
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from arete.application.utils.common import sanitize
from arete.application.utils.fs import iter_markdown_files
from arete.application.utils.text import parse_frontmatter, rebuild_markdown_with_frontmatter
from arete.domain.interfaces import ContentCache
from arete.domain.models import UpdateItem

# _quick_check_file reasons for an Arete file whose cards (and nids) could not be read.
UNREADABLE_REASONS = ("read_error", "no_or_bad_yaml", "no_deck")


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

    def scan_for_compatible_files(self) -> Iterable[tuple[Path, dict[str, Any], bool]]:
        """Iterate over all markdown files in the vault, check them for validity.

        A file counts when its frontmatter has `arete: true` and a non-empty `cards` list.
        Return: (path, meta, is_fresh)
                 is_fresh=True means we just parsed it (cache was cold/dirty).
                 is_fresh=False means we loaded meta from stat-cache (cache was warm).
        """
        self.unreadable = []
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

    def _quick_check_file(
        self, md_file: Path
    ) -> tuple[bool, int, str | None, dict[str, Any] | None, bool]:
        # Returns: (ok, num_cards, reason, meta, is_fresh)
        try:
            st = md_file.stat()
            mtime = st.st_mtime
            size = st.st_size
        except Exception as e:
            return (False, 0, f"stat_error:{e}", None, True)

        if self.cache and not self.ignore_cache:
            try:
                cached_meta = self.cache.get_file_meta_by_stat(md_file, mtime, size)
                if cached_meta and cached_meta.get("arete") is True:
                    cards = cached_meta.get("cards", [])
                    return (True, len(cards), None, cached_meta, False)
            except Exception as e:
                self.logger.warning(f"[vault] cache miss (error) for {md_file.name}: {e}")

        try:
            text = md_file.read_text(encoding="utf-8", errors="strict")
        except Exception as e:
            return (False, 0, f"read_error:{e}", None, True)

        # Only parse files that could be Arete notes; the parse below decides. The marker
        # can sit after a long cards block, so the whole text is searched, not a prefix.
        if "arete:" not in text:
            return (False, 0, "not_arete_file", None, True)

        meta, _body = parse_frontmatter(text)
        if not meta or "__yaml_error__" in meta:
            return (False, 0, "no_or_bad_yaml", None, True)
        if meta.get("arete") is not True:
            return (False, 0, "not_arete_file", None, True)

        cards = meta.get("cards", [])
        if not isinstance(cards, list) or not cards:
            return (False, 0, "no_cards", None, True)

        deck = meta.get("deck")
        # basic check
        has_any_card_deck = any(isinstance(c, dict) and c.get("deck") for c in cards)
        if not deck and not has_any_card_deck:
            # If force-syncing, we accept it for normalization even if it won't sync to Anki
            if not self.ignore_cache:
                return (False, 0, "no_deck", None, True)
            else:
                self.logger.debug(
                    f"[vault] {md_file.name}: no deck, but accepted for normalization (--force)"
                )

        # The stat cache is written by record_synced, after the file's cards reached Anki.
        # Writing it here let a dry run, or a run where a card failed, hide the edit from
        # every later sync.
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
                text = md_path.read_text(encoding="utf-8")
                meta, _ = parse_frontmatter(text)
                if not meta or "__yaml_error__" in meta:
                    continue
                st = md_path.stat()
                file_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
                self.cache.set_file_meta(
                    md_path, file_hash, meta, mtime=st.st_mtime, size=st.st_size
                )
            except Exception as e:
                self.logger.warning(f"[cache] could not record {md_path.name} as synced: {e}")

    def format_vault(self, dry_run: bool = False) -> int:
        """Scan and re-serialize all compatible files to normalize YAML.

        Return the number of files updated.
        """
        count = 0
        for md_path, meta, _is_fresh in self.scan_for_compatible_files():
            try:
                text = md_path.read_text(encoding="utf-8")
                _meta, body = parse_frontmatter(text)

                # Rebuild using our dumper (which now uses |-)
                new_text = rebuild_markdown_with_frontmatter(meta, body)

                if new_text != text:
                    count += 1
                    if dry_run:
                        self.logger.info(f"[dry-run] Would format {md_path.name}")
                    else:
                        md_path.write_text(new_text, encoding="utf-8")
                        self.logger.debug(f"[format] {md_path.name}: normalized YAML")
            except Exception as e:
                self.logger.error(f"[error] formatting {md_path.name}: {e}")

        return count

    def apply_updates(self, updates: list[UpdateItem], dry_run: bool = False):
        """Write back new NIDs/CIDs to the markdown files."""
        by_file: dict[Path, list[UpdateItem]] = defaultdict(list)
        for u in updates:
            if u.ok and (u.new_nid or u.new_cid):
                by_file[u.source_file].append(u)

        for md_path, ups in by_file.items():
            try:
                text = md_path.read_text(encoding="utf-8")
                meta, body = parse_frontmatter(text)
                if not meta or "__yaml_error__" in meta:
                    continue
                cards = meta.get("cards", [])
                changed = False
                for u in ups:
                    i = self._card_position(cards, u)
                    if i is not None:
                        card_data = cards[i]
                        # V2 format: write nid/cid into anki block
                        anki_block = card_data.get("anki", {})
                        if not isinstance(anki_block, dict):
                            anki_block = {}

                        if u.new_nid and sanitize(anki_block.get("nid", "")) != u.new_nid:
                            anki_block["nid"] = u.new_nid
                            changed = True
                        if u.new_cid and sanitize(anki_block.get("cid", "")) != u.new_cid:
                            anki_block["cid"] = u.new_cid
                            changed = True

                        if anki_block:
                            card_data["anki"] = anki_block
                # FORCE FIX: If we are ignoring cache (force sync), always mark as changed
                # to trigger a rewrite with normalized YAML (|- block style).
                if self.ignore_cache:
                    changed = True

                if changed:
                    meta["cards"] = cards
                    new_text = rebuild_markdown_with_frontmatter(meta, body)
                    if new_text != text:
                        if dry_run:
                            self.logger.info(f"[dry-run] Would write normalized YAML to {md_path}")
                        else:
                            md_path.write_text(new_text, encoding="utf-8")
                            self.logger.debug(
                                f"[write] {md_path}: persisted nid/cid into frontmatter"
                            )
            except Exception as e:
                self.logger.error(f"[error] write-updates {md_path}: {e}")
