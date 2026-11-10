import logging
from pathlib import Path
from typing import Any

from arete.application.sync.converter import markdown_to_anki_html
from arete.application.utils.common import sanitize, to_list
from arete.application.utils.media import transform_images_in_text, transform_wikilinks_to_uri
from arete.application.utils.text import card_content_hash
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
        # nid -> the cards in other files that claim it too; set by the pipeline.
        self.contested_nids: dict[str, list[str]] = {}
        # casefolded Arete id -> every card that declares it, when more than one does; set by
        # the pipeline. Anki matches tags case-insensitively, so case variants are one id.
        self.duplicate_ids: dict[str, list[str]] = {}

    @staticmethod
    def _extract_raw_nid(card: dict[str, Any]) -> str | None:
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

    def _cached_nid(self, md_path: Path, idx: int, cache: ContentCache) -> str | None:
        """The nid the content cache recorded for a card, which the vault may not hold yet."""
        import json

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
            raw = cls._extract_raw_nid(card)
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

    def parse_file(
        self,
        md_path: Path,
        meta: dict[str, Any],
        cache: ContentCache,
        name_index: dict[str, list[Path]] | None = None,
        is_fresh: bool = True,
    ) -> tuple[list[AnkiNote], list[int], list[dict[str, str | None]]]:
        import json

        deck_frontmatter = sanitize(meta.get("deck", "")) or None
        default_model = sanitize(meta.get("model", "Basic"))
        base_tags = [t.strip() for t in to_list(meta.get("tags", [])) if t and t.strip()]
        cards = meta.get("cards", [])

        self.logger.debug(f"[parser] Parsing {md_path.name}. cards={len(cards)} fresh={is_fresh}")

        # Arete writes anki.nid/anki.cid itself after a successful sync; these
        # fields are never meant to be authored by hand. When they are (e.g.
        # AI-generated or copy-pasted cards), the ids are frequently fabricated
        # and would make sync UPDATE an unrelated note instead of creating one.
        # Pre-scan for nids claimed by more than one card so we can reject them.
        duplicate_nids = self._find_duplicate_nids(cards)

        notes: list[AnkiNote] = []
        skipped_indices: list[int] = []
        inventory: list[dict[str, str | None]] = []

        for idx, card in enumerate(cards, start=1):
            try:
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
                    skipped_indices.append(idx)
                    continue

                # OPTIMIZATION: Hot Cache Lookup
                # If file metadata hasn't changed (is_fresh=False), check if we have a fully
                # rendered note in the 'cards' table. If so, we can skip parsing/rendering.
                if not is_fresh and not self.ignore_cache:
                    cached_note_data = cache.get_note(md_path, idx)
                    if cached_note_data:
                        try:
                            # cached_note_data is (hash, note_json)
                            _, note_json = cached_note_data
                            if note_json:
                                cached_note = AnkiNote.from_dict(json.loads(note_json))
                                # The entry is only written after Anki accepted the note,
                                # so an unchanged file has nothing to send; prune still
                                # needs it.
                                inventory.append({"nid": cached_note.nid, "deck": cached_note.deck})
                                continue
                        except Exception as e:
                            self.logger.warning(
                                f"Failed to load hot cache for {md_path}#{idx}: {e}"
                            )

                model = sanitize(card.get("model", default_model))
                mlow = model.lower()
                fields = {}

                # Field validation logic
                if mlow == "basic":
                    f_val = card.get("Front") or ""
                    b_val = card.get("Back") or ""
                    fields = {
                        "Front": sanitize(f_val),
                        "Back": sanitize(b_val),
                    }
                    if not fields["Front"] or not fields["Back"]:
                        self.logger.warning(
                            f"[skip] {md_path} card#{idx}: Basic requires Front & Back"
                        )
                        skipped_indices.append(idx)
                        continue
                elif mlow == "cloze":
                    t_val = card.get("Text") or ""
                    e_val = card.get("Back Extra") or ""
                    fields = {
                        "Text": sanitize(t_val),
                        "Back Extra": sanitize(e_val),
                    }
                    if not fields["Text"]:
                        self.logger.warning(f"[skip] {md_path} card#{idx}: Cloze requires Text")
                        skipped_indices.append(idx)
                        continue
                else:
                    # Arete's own keys are not Anki fields. Sent as fields they would enter the
                    # hash, and a card would re-sync whenever its line moved.
                    _exclude = {
                        "model",
                        "deck",
                        "tags",
                        "id",
                        "deps",
                        "anki",
                        "__line__",
                    }
                    fields = {k: sanitize(v) for k, v in card.items() if k not in _exclude}

                    if not fields:
                        self.logger.warning(
                            f"[skip] {md_path} card#{idx}: custom model '{model}' has no fields"
                        )
                        skipped_indices.append(idx)
                        continue

                # 1) Convert math + images
                for fk, fv in list(fields.items()):
                    if isinstance(fv, str) and fv:
                        txt = fv

                        # 2. Image copying
                        txt = transform_images_in_text(
                            txt,
                            md_path,
                            self.vault_root,
                            self.anki_media_dir,
                            self.logger,
                            name_index=name_index,
                        )

                        # 3. Wikilink → Obsidian URI (before markdown rendering)
                        txt = transform_wikilinks_to_uri(txt, self.vault_root.name)

                        # 4. Markdown -> HTML (Render here to unblock Consumer)
                        # We use the converter which includes MathJax protection
                        fields[fk] = markdown_to_anki_html(txt)

                # 2) IDs from anki block
                anki_block = card.get("anki", {}) if isinstance(card.get("anki"), dict) else {}
                nid = self._extract_raw_nid(card)
                cid = sanitize(anki_block.get("cid", "")).strip() or None
                start_line = int(card.get("__line__", 0))

                # Discard provably-invalid ids so a fabricated value can't make
                # sync overwrite an unrelated note (see _validate_card_ids).
                nid, cid = self._validate_card_ids(nid, cid, duplicate_nids, md_path, idx)

                # 3) Deck
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
                    skipped_indices.append(idx)
                    continue

                # 4) Add Obsidian source location for linking back
                try:
                    relative_path = md_path.relative_to(self.vault_root)
                    vault_name = self.vault_root.name
                    arete_id = sanitize(card.get("id", "")).strip()
                    # Store as: vault|path|line|arete_id
                    # Use start_line so Advanced URI can jump to the exact location
                    fields["_obsidian_source"] = (
                        f"{vault_name}|{relative_path.as_posix()}|{start_line}|{arete_id}"
                    )
                except ValueError:
                    # File not in vault root, skip source field
                    pass

                # Construct per-card tag list (file base + card-level + arete ID)
                card_tags = list(base_tags)  # Copy to avoid mutating shared list
                card_tags.extend(
                    t.strip() for t in to_list(card.get("tags") or []) if t and t.strip()
                )
                card_id = sanitize(card.get("id", "")).strip()
                if card_id:
                    card_tags.append(card_id)  # ID already has arete_ prefix
                # Dedupe while preserving order (file/card overlap, arete ID)
                card_tags = list(dict.fromkeys(card_tags))

                # 5) Hash what Anki will receive, the per-card tags included, so a card-level
                # tag edit changes the hash.
                content_hash = card_content_hash(model, deck_this, card_tags, fields, nid, cid)

                cached_hash = cache.get_hash(md_path, idx)
                if not self.ignore_cache and cached_hash == content_hash:
                    self.logger.debug(f"[cache-hit] {md_path} card#{idx}: skipping")
                    # The cached note holds the nid Anki assigned, which the vault may not
                    # hold yet (a crash before write-back). Prune must count it as claimed.
                    if cached_nid := self._cached_nid(md_path, idx, cache):
                        inventory.append({"nid": cached_nid, "deck": None})
                    continue

                note_obj = AnkiNote(
                    model=model,
                    deck=deck_this,
                    fields=fields,
                    tags=card_tags,
                    start_line=start_line,
                    end_line=start_line,  # Frontmatter cards are single-block usually
                    nid=nid,
                    cid=cid,
                    content_hash=content_hash,
                    source_file=md_path,
                    source_index=idx,
                    arete_id=card_id or None,
                )
                notes.append(note_obj)
                # The content cache is written by the pipeline AFTER Anki accepts the note.
                # Writing it here marked a card as synced before the sync ran, so a failed
                # sync or a --dry-run left the card permanently skipped.

            except Exception as e:
                self.logger.error(f"[error] {md_path} card#{idx}: {e}")
                skipped_indices.append(idx)

        # A skipped card (empty field, bad model, render error) still owns its Anki note:
        # the user is mid-edit, not deleting it. Protect its nid from --prune.
        for idx in skipped_indices:
            skipped = cards[idx - 1]
            if isinstance(skipped, dict) and (nid := self._extract_raw_nid(skipped)):
                inventory.append({"nid": nid, "deck": None})

        self.logger.debug(
            f"[parser] Finished {md_path.name}. notes={len(notes)}, "
            f"skipped={len(skipped_indices)}, inventory={len(inventory)}"
        )
        return notes, skipped_indices, inventory
