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


class VaultService:
    def __init__(self, root: Path, cache: ContentCache, ignore_cache: bool = False):
        self.root = root
        self.cache = cache
        self.ignore_cache = ignore_cache
        self.logger = logging.getLogger(__name__)

    def scan_for_compatible_files(self) -> Iterable[tuple[Path, dict[str, Any], bool]]:
        """Iterates over all markdown files in the vault, checks them for validity
        (frontmatter, version), and yields valid files.
        Returns: (path, meta, is_fresh)
                 is_fresh=True means we just parsed it (cache was cold/dirty).
                 is_fresh=False means we loaded meta from stat-cache (cache was warm).
        """
        for p in iter_markdown_files(self.root):
            ok, _, reason, meta, is_fresh = self._quick_check_file(p)
            if ok and meta:
                cards_count = len(meta.get("cards", []))
                self.logger.debug(
                    f"[vault] Accepted {p.name} (v{meta.get('anki_template_version')}) "
                    f"cards={cards_count} fresh={is_fresh}"
                )
                yield p, meta, is_fresh
            else:
                self.logger.debug(f"[vault] Skipped {p.name}: {reason}")

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
                if cached_meta:
                    cards = cached_meta.get("cards", [])
                    return (True, len(cards), None, cached_meta, False)
            except Exception:
                pass

        try:
            text = md_file.read_text(encoding="utf-8", errors="strict")
            file_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
        except Exception as e:
            return (False, 0, f"read_error:{e}", None, True)

        # Heuristic: only parse if it looks like an arete file
        # We check the first 2KB for efficiency
        header = text[:2048].lower()
        if (
            "arete:" not in header
            and "anki_template_version:" not in header
            and "anki_plugin_version:" not in header
            and "cards:" not in header
        ):
            return (False, 0, "not_arete_file", None, True)

        meta, _body = parse_frontmatter(text)
        if not meta or "__yaml_error__" in meta:
            return (False, 0, "no_or_bad_yaml", None, True)

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

        # Save to cache
        if self.cache:
            self.cache.set_file_meta(md_file, file_hash, meta, mtime=mtime, size=size)

        return (True, len(cards), None, meta, True)

    def format_vault(self, dry_run: bool = False) -> int:
        """Scans and re-serializes all compatible files to normalize YAML.
        Returns the number of files updated.
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

                        # Update cache
                        if self.cache:
                            new_hash = hashlib.md5(new_text.encode("utf-8")).hexdigest()
                            st = md_path.stat()
                            self.cache.set_file_meta(
                                md_path, new_hash, meta, mtime=st.st_mtime, size=st.st_size
                            )
            except Exception as e:
                self.logger.error(f"[error] formatting {md_path.name}: {e}")

        return count

    def apply_updates(self, updates: list[UpdateItem], dry_run: bool = False):
        """Writes back new NIDs/CIDs to the markdown files."""
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
                    i = u.source_index - 1
                    if 0 <= i < len(cards):
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

                        # Also migrate legacy root-level nid/cid to anki block
                        for legacy_key in ["nid", "cid"]:
                            if legacy_key in card_data and legacy_key not in ["anki"]:
                                if legacy_key not in anki_block:
                                    anki_block[legacy_key] = card_data[legacy_key]
                                del card_data[legacy_key]
                                card_data["anki"] = anki_block
                                changed = True
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

                        # Fix for Hot Sync: Update cache immediately since we changed mtime!
                        if self.cache:
                            try:
                                new_hash = hashlib.md5(new_text.encode("utf-8")).hexdigest()
                                st = md_path.stat()
                                self.cache.set_file_meta(
                                    md_path, new_hash, meta, mtime=st.st_mtime, size=st.st_size
                                )
                            except Exception as e:
                                self.logger.warning(
                                    f"Failed to update cache after write for {md_path}: {e}"
                                )
            except Exception as e:
                self.logger.error(f"[error] write-updates {md_path}: {e}")
