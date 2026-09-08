import hashlib
import logging
from pathlib import Path
from typing import Any

from arete.application.sync.converter import markdown_to_anki_html
from arete.application.utils.common import sanitize, to_list
from arete.application.utils.media import transform_images_in_text, transform_wikilinks_to_uri
from arete.application.utils.text import make_editor_note
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

    @staticmethod
    def _extract_raw_nid(card: dict[str, Any]) -> str | None:
        """Read a card's declared nid from the v2 ``anki`` block."""
        anki_block = card.get("anki") if isinstance(card.get("anki"), dict) else {}
        raw = sanitize((anki_block or {}).get("nid", "")).strip()
        return raw or None

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
        """Reject provably-invalid Anki ids so sync can't hijack the wrong note.

        ``anki.nid``/``anki.cid`` are written by Arete after a sync and are not
        meant to be authored by hand. One situation is impossible in a real Anki
        collection and therefore indicates fabricated/copy-pasted metadata: a
        ``nid`` shared by more than one card in the same file (a note id
        identifies exactly one note).

        When detected, the suspect nid is discarded (returning ``None``) and a
        warning is logged, so the card is matched by its arete id / content
        (created fresh or healed) rather than overwriting an unrelated note. We
        never rewrite the user's file here; Arete persists a real nid on the
        next successful sync as it normally does.

        Note: ``nid == cid`` is NOT a fabrication signal. For single-card note
        types Anki assigns the first card's id equal to the note id, so the two
        coincide for the large majority of normal notes (~80%+ of a real vault)
        and equality must be trusted — stripping it would re-create the note as
        a duplicate on the next sync of an edited card.
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
                                notes.append(cached_note)
                                # Also need to track inventory for prune logic
                                inventory.append({"nid": cached_note.nid, "deck": cached_note.deck})
                                # Log as deep cache hit (maybe trace level)
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
                    # Check for "Front"/"front" and "Back"/"back"
                    f_val = card.get("Front") or card.get("front") or ""
                    b_val = card.get("Back") or card.get("back") or ""
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
                    # Check for "Text"/"text" and "Back Extra"/"back extra"/"Extra"/"extra"
                    t_val = card.get("Text") or card.get("text") or ""
                    e_val = (
                        card.get("Back Extra")
                        or card.get("back extra")
                        or card.get("Extra")
                        or card.get("extra")
                        or ""
                    )
                    fields = {
                        "Text": sanitize(t_val),
                        "Back Extra": sanitize(e_val),
                    }
                    if not fields["Text"]:
                        self.logger.warning(f"[skip] {md_path} card#{idx}: Cloze requires Text")
                        skipped_indices.append(idx)
                        continue
                else:
                    # Allow 'nid' to pass through for custom models so it can be stored in Anki
                    _exclude = {"cid", "model", "deck", "tags", "markdown"}
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
                deck_this = (
                    sanitize(card.get("deck", deck_frontmatter))
                    if deck_frontmatter
                    else sanitize(card.get("deck"))
                )
                if not deck_this:
                    deck_this = self.default_deck
                    self.logger.debug(
                        f"[info] {md_path} card#{idx}: no deck set, using default: {deck_this}"
                    )

                # NEW: Track as valid inventory for Prune Mode
                # We must record the deck even if NID is missing (to protect the deck from deletion)
                inventory.append({"nid": nid, "deck": deck_this})

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

                # 5) Calculate hash check
                # We use make_editor_note to produce the canonical content for hashing
                content = make_editor_note(
                    model, deck_this, base_tags, fields, nid=nid, cid=cid, markdown=True
                )
                content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()

                cached_hash = cache.get_hash(md_path, idx)
                if not self.ignore_cache and cached_hash == content_hash:
                    self.logger.debug(f"[cache-hit] {md_path} card#{idx}: skipping")
                    continue

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
                )
                notes.append(note_obj)

                # OPTIMIZATION: Save Deep Cache
                # We save the fully rendered object so next time we can skip everything
                try:
                    if not self.ignore_cache:
                        note_json = json.dumps(note_obj.to_dict())
                        cache.set_note(md_path, idx, content_hash, note_json)
                except Exception as e_cache:
                    self.logger.warning(f"Failed to save deep cache for {md_path}: {e_cache}")

            except Exception as e:
                self.logger.error(f"[error] {md_path} card#{idx}: {e}")
                skipped_indices.append(idx)

        self.logger.debug(
            f"[parser] Finished {md_path.name}. notes={len(notes)}, "
            f"skipped={len(skipped_indices)}, inventory={len(inventory)}"
        )
        return notes, skipped_indices, inventory
