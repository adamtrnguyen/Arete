import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from arete.application.sync.converter import RENDERER_LIBRARY, markdown_to_anki_html
from arete.application.utils.common import sanitize, to_list
from arete.application.utils.media import transform_images_in_text, transform_wikilinks_to_uri
from arete.application.utils.text import card_content_hash
from arete.domain.constants import RENDER_VERSION
from arete.domain.interfaces import ContentCache
from arete.domain.models import AnkiNote


class MarkdownParser:
    def __init__(
        self,
        vault_root: Path,
        anki_media_dir: Path,
        ignore_cache: bool = False,
        default_deck: str = "Default",
        logger=None,
    ):
        """Initialize MarkdownParser."""
        self.vault_root = vault_root
        self.anki_media_dir = anki_media_dir
        self.ignore_cache = ignore_cache
        self.default_deck = default_deck
        self.logger = logger or logging.getLogger(__name__)
        # Everything a render reads besides the card and its file: a cached render made
        # under other values (another vault root or name, media folder, default deck,
        # renderer or markdown library) is stale, though the card is not.
        self.render_key = hashlib.sha256(
            json.dumps(
                [
                    RENDER_VERSION,
                    RENDERER_LIBRARY,
                    str(vault_root),
                    vault_root.name,
                    str(anki_media_dir),
                    default_deck,
                ]
            ).encode()
        ).hexdigest()[:16]
        # nid -> the cards in other files that claim it too; set by the pipeline.
        self.contested_nids: dict[str, list[str]] = {}
        # casefolded Arete id -> every card that declares it, when more than one does; set by
        # the pipeline. Anki matches tags case-insensitively, so case variants are one id.
        self.duplicate_ids: dict[str, list[str]] = {}

    @staticmethod
    def extract_raw_nid(card: dict[str, Any]) -> str | None:
        """Read a card's declared nid from its ``anki`` block."""
        anki_block = card.get("anki") if isinstance(card.get("anki"), dict) else {}
        raw = sanitize((anki_block or {}).get("nid", "")).strip()
        return raw or None

    @staticmethod
    def declared_id(card: Any) -> str | None:
        """A card's ``id:`` as the vault holds it, or None."""
        if not isinstance(card, dict):
            return None
        return sanitize(card.get("id", "")).strip() or None

    def _cached_render(
        self,
        md_path: Path,
        idx: int,
        card: dict[str, Any],
        cache: ContentCache,
        duplicate_nids: set[str],
    ) -> AnkiNote | None:
        """The card as the last sync sent it, when that is still the vault's card.

        The cached render counts only if it was made under this parser's `render_key` and
        its nid, cid and Arete id are the ones the vault holds now; a nid other files claim
        too is never taken from it. Otherwise None, and the card is rendered from its
        markdown.
        """
        entry = cache.get_note(md_path, idx)
        if not (entry and entry[1]):
            return None
        try:
            data = json.loads(entry[1])
            if data.get("render_key") != self.render_key:
                return None
            cached = AnkiNote.from_dict(data)
        except Exception as e:
            self.logger.warning(f"Failed to load hot cache for {md_path}#{idx}: {e}")
            return None
        nid = self.extract_raw_nid(card)
        anki_block = card.get("anki") if isinstance(card.get("anki"), dict) else {}
        cid = sanitize((anki_block or {}).get("cid", "")).strip() or None
        if nid and nid in duplicate_nids:
            nid, cid = None, None
        if (cached.nid, cached.cid, cached.arete_id) != (nid, cid, self.declared_id(card)):
            return None
        if nid and nid in self.contested_nids:
            return None
        cached.source_file, cached.source_index, cached.unchanged = md_path, idx, True
        return cached

    def source_field(self, md_path: Path, line: int, arete_id: str) -> str | None:
        """The `_obsidian_source` value: `vault|path|line|arete_id`; None outside the vault.

        The add-on's "Open in Obsidian" jumps to `line` through Advanced URI.
        """
        try:
            relative_path = md_path.relative_to(self.vault_root)
        except ValueError:
            return None
        return f"{self.vault_root.name}|{relative_path.as_posix()}|{line}|{arete_id}"

    def _cached_nid(self, md_path: Path, idx: int, cache: ContentCache) -> str | None:
        """The nid the content cache recorded for a card, which the vault may not hold yet."""
        cached_entry = cache.get_note(md_path, idx)
        if not (cached_entry and cached_entry[1]):
            return None
        try:
            return AnkiNote.from_dict(json.loads(cached_entry[1])).nid
        except Exception as e:
            self.logger.warning(f"Failed to read cached nid for {md_path}#{idx}: {e}")
            return None

    @classmethod
    def _find_duplicate_nids(cls, cards: list[Any]) -> set[str]:
        """Return nids declared by more than one card in this file.

        A note id uniquely identifies a single Anki note, so the same nid
        appearing on two cards is impossible in a real collection and signals
        fabricated/copy-pasted metadata.
        """
        counts: dict[str, int] = {}
        for card in cards:
            if not isinstance(card, dict):
                continue
            raw = cls.extract_raw_nid(card)
            if raw:
                counts[raw] = counts.get(raw, 0) + 1
        return {nid for nid, n in counts.items() if n > 1}

    def _validate_card_ids(
        self,
        nid: str | None,
        cid: str | None,
        duplicate_nids: set[str],
        md_path: Path,
        idx: int,
    ) -> tuple[str | None, str | None]:
        """``(nid, cid)``, or ``(None, None)`` when ``nid`` is shared by another card in the file.

        A note id identifies one note, so a shared nid is fabricated or copy-pasted. The card
        is then matched by its arete id instead of overwriting an unrelated note. Logs a
        warning; never rewrites the file.

        ``nid == cid`` is not a fabrication signal: Anki gives a single-card note's first card
        the note's id, so most real notes have it. Stripping it would re-create the note as a
        duplicate.
        """
        if nid and nid in duplicate_nids:
            self.logger.warning(
                f"[meta] {md_path.name} card#{idx}: anki.nid {nid} is shared by another "
                "card in this file; ignoring it so the card syncs as new instead of "
                "overwriting an unrelated note."
            )
            return None, None
        return nid, cid

    _ARETE_CARD_KEYS = frozenset({"model", "deck", "tags", "id", "deps", "anki", "__line__"})

    def _extract_card_fields(
        self, card: dict[str, Any], model: str, md_path: Path, idx: int
    ) -> dict[str, str] | None:
        """Extract and validate Anki fields for a card, or return None if invalid."""
        mlow = model.lower()
        if mlow == "basic":
            fields = {
                "Front": sanitize(card.get("Front") or ""),
                "Back": sanitize(card.get("Back") or ""),
            }
            if not fields["Front"] or not fields["Back"]:
                self.logger.warning(f"[skip] {md_path} card#{idx}: Basic requires Front & Back")
                return None
            return fields
        if mlow == "cloze":
            fields = {
                "Text": sanitize(card.get("Text") or ""),
                "Back Extra": sanitize(card.get("Back Extra") or ""),
            }
            if not fields["Text"]:
                self.logger.warning(f"[skip] {md_path} card#{idx}: Cloze requires Text")
                return None
            return fields
        # Arete's own keys are not Anki fields. Sent as fields they would enter the
        # hash, and a card would re-sync whenever its line moved.
        fields = {k: sanitize(v) for k, v in card.items() if k not in self._ARETE_CARD_KEYS}
        if not fields:
            self.logger.warning(
                f"[skip] {md_path} card#{idx}: custom model '{model}' has no fields"
            )
            return None
        return fields

    def _render_fields(
        self,
        fields: dict[str, str],
        md_path: Path,
        name_index: dict[str, list[Path]] | None,
    ) -> None:
        """Transform images, wikilinks, and markdown/MathJax in-place for each non-empty field."""
        for fk, fv in list(fields.items()):
            if isinstance(fv, str) and fv:
                txt = transform_images_in_text(
                    fv,
                    md_path,
                    self.vault_root,
                    self.anki_media_dir,
                    self.logger,
                    name_index=name_index,
                )
                txt = transform_wikilinks_to_uri(txt, self.vault_root.name)
                fields[fk] = markdown_to_anki_html(txt)

    @staticmethod
    def _build_card_tags(base_tags: list[str], card: dict[str, Any], card_id: str) -> list[str]:
        """Merge file tags, card tags, and Arete ID while preserving order without duplicates."""
        card_tags = list(base_tags)
        card_tags.extend(t.strip() for t in to_list(card.get("tags") or []) if t and t.strip())
        if card_id:
            card_tags.append(card_id)  # ID already has arete_ prefix
        return list(dict.fromkeys(card_tags))

    def _check_deep_cache(
        self,
        md_path: Path,
        idx: int,
        content_hash: str,
        cache: ContentCache,
        inventory: list[dict[str, str | None]],
    ) -> bool:
        """Return whether the rendered hash matches the last synced hash, protecting cached nids."""
        if self.ignore_cache or cache.get_hash(md_path, idx) != content_hash:
            return False
        self.logger.debug(f"[cache-hit] {md_path} card#{idx}: unchanged")
        # The cached note holds the nid Anki assigned, which the vault may not hold yet
        # (a crash before write-back). Prune must count it as claimed.
        if cached_nid := self._cached_nid(md_path, idx, cache):
            inventory.append({"nid": cached_nid, "deck": None})
        return True

    def _parse_card(
        self,
        md_path: Path,
        idx: int,
        card: dict[str, Any],
        *,
        deck_frontmatter: str | None,
        default_model: str,
        base_tags: list[str],
        duplicate_nids: set[str],
        cache: ContentCache,
        name_index: dict[str, list[Path]] | None,
        is_fresh: bool,
        inventory: list[dict[str, str | None]],
    ) -> AnkiNote | None:
        """Parse one card dict into an AnkiNote, updating `inventory`; None when skipped."""
        card_id = self.declared_id(card)
        if card_id and card_id.casefold() in self.duplicate_ids:
            # Several cards declare this id: each would claim the others' note.
            # None is sent; every declared or cached nid stays in the inventory,
            # so prune keeps the notes.
            others = ", ".join(self.duplicate_ids[card_id.casefold()])
            self.logger.error(
                f"[conflict] {md_path.name} card#{idx}: Arete id {card_id} is declared "
                f"by {others}. Not syncing these cards; give each card its own id."
            )
            deck_this = sanitize(card.get("deck") or deck_frontmatter or "")
            inventory.append(
                {
                    "nid": self._cached_nid(md_path, idx, cache),
                    "deck": deck_this or self.default_deck,
                }
            )
            return None

        # An unchanged file's card is rendered from the content cache instead of
        # its markdown. It is still yielded: every card is compared with Anki.
        if not is_fresh and not self.ignore_cache:
            cached = self._cached_render(md_path, idx, card, cache, duplicate_nids)
            if cached is not None:
                inventory.append({"nid": cached.nid, "deck": cached.deck})
                return cached

        model = sanitize(card.get("model") or default_model) or default_model
        fields = self._extract_card_fields(card, model, md_path, idx)
        if fields is None:
            return None
        self._render_fields(fields, md_path, name_index)

        anki_block = card.get("anki") if isinstance(card.get("anki"), dict) else {}
        nid = self.extract_raw_nid(card)
        cid = sanitize((anki_block or {}).get("cid", "")).strip() or None
        start_line = int(card.get("__line__", 0))
        nid, cid = self._validate_card_ids(nid, cid, duplicate_nids, md_path, idx)

        # `or`, not a get() default: an empty `deck: ''` on a card takes the file's
        # deck, not Default.
        deck_this = sanitize(card.get("deck") or deck_frontmatter or "")
        if not deck_this:
            deck_this = self.default_deck
            self.logger.debug(
                f"[info] {md_path} card#{idx}: no deck set, using default: {deck_this}"
            )

        # Prune inventory: record the deck even without a nid, so prune keeps the deck.
        inventory.append({"nid": nid, "deck": deck_this})

        if nid and nid in self.contested_nids:
            # Cards in two files claim one note: sending either would overwrite
            # the other's. The note stays in the inventory, so prune keeps it.
            others = ", ".join(self.contested_nids[nid])
            self.logger.error(
                f"[conflict] {md_path.name} card#{idx}: anki.nid {nid} is claimed by "
                f"{others}. Not syncing these cards; keep the anki: block only on the "
                "card that owns the note."
            )
            return None

        raw_card_id = sanitize(card.get("id", "")).strip()
        if (source := self.source_field(md_path, start_line, raw_card_id)) is not None:
            fields["_obsidian_source"] = source

        card_tags = self._build_card_tags(base_tags, card, raw_card_id)
        content_hash = card_content_hash(model, deck_this, card_tags, fields, nid, cid)
        unchanged = self._check_deep_cache(md_path, idx, content_hash, cache, inventory)

        # The content cache is written by the pipeline AFTER Anki accepts the note.
        # Writing it here marked a card as synced before the sync ran, so a failed
        # sync or a --dry-run left the card permanently skipped.
        return AnkiNote(
            model=model,
            deck=deck_this,
            fields=fields,
            tags=card_tags,
            start_line=start_line,
            nid=nid,
            cid=cid,
            content_hash=content_hash,
            source_file=md_path,
            source_index=idx,
            arete_id=raw_card_id or None,
            render_key=self.render_key,
            unchanged=unchanged,
        )

    def parse_file(
        self,
        md_path: Path,
        meta: dict[str, Any],
        cache: ContentCache,
        name_index: dict[str, list[Path]] | None = None,
        is_fresh: bool = True,
    ) -> tuple[list[AnkiNote], list[int], list[dict[str, str | None]]]:
        deck_frontmatter = sanitize(meta.get("deck", "")) or None
        default_model = sanitize(meta.get("model") or "Basic") or "Basic"
        base_tags = [t.strip() for t in to_list(meta.get("tags", [])) if t and t.strip()]
        cards = meta.get("cards", [])

        self.logger.debug(f"[parser] Parsing {md_path.name}. cards={len(cards)} fresh={is_fresh}")

        # Arete writes anki.nid/anki.cid itself after a successful sync; these
        # fields are never meant to be authored by hand. Pre-scan for nids claimed
        # by more than one card so we can reject fabricated/copy-pasted ids.
        duplicate_nids = self._find_duplicate_nids(cards)

        notes: list[AnkiNote] = []
        skipped_indices: list[int] = []
        inventory: list[dict[str, str | None]] = []

        for idx, card in enumerate(cards, start=1):
            try:
                note = self._parse_card(
                    md_path,
                    idx,
                    card,
                    deck_frontmatter=deck_frontmatter,
                    default_model=default_model,
                    base_tags=base_tags,
                    duplicate_nids=duplicate_nids,
                    cache=cache,
                    name_index=name_index,
                    is_fresh=is_fresh,
                    inventory=inventory,
                )
                if note is None:
                    skipped_indices.append(idx)
                else:
                    notes.append(note)
            except Exception as e:
                self.logger.error(f"[error] {md_path} card#{idx}: {e}")
                skipped_indices.append(idx)

        # A skipped card (empty field, bad model, render error) still owns its Anki note:
        # the user is mid-edit, not deleting it. Protect its nid from --prune.
        for idx in skipped_indices:
            skipped = cards[idx - 1]
            if isinstance(skipped, dict) and (nid := self.extract_raw_nid(skipped)):
                inventory.append({"nid": nid, "deck": None})

        self.logger.debug(
            f"[parser] Finished {md_path.name}. notes={len(notes)}, "
            f"skipped={len(skipped_indices)}, inventory={len(inventory)}"
        )
        return notes, skipped_indices, inventory
