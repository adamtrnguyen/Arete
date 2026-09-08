"""One place that knows the shape of a card, a note and a config.

Every test used to hand-write these. 28 `AnkiNote(...)` literals across 8 files, raw
card YAML in 23 files, and the app config built 8 different ways. The cost showed up
in the Obsidian plugin, where three separate test files each hand-wrote a card with a
flat `nid:` key. Arete writes `anki: {nid: ...}` nested, and has for a long time. All
three files agreed with each other and disagreed with the product, so they passed
while testing nothing.

A factory only helps if it stays true, so `tests/test_factories.py` feeds this
module's output through Arete's own parser and fails when the two disagree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from arete.application.config import AppConfig
from arete.domain.models import AnkiNote

DEFAULT_DECK = "Test::Deck"
DEFAULT_MODEL = "Basic"


def card(
    *,
    front: str = "What is a port?",
    back: str = "What a caller needs, not how it is done.",
    arete_id: str | None = "arete_01TESTCARD0000000000000001",
    nid: str | int | None = None,
    cid: str | int | None = None,
    deck: str | None = None,
    model: str | None = None,
    tags: list[str] | None = None,
    requires: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One card, in the frontmatter shape Arete reads and writes.

    Note the nesting: ids live under `anki`, never at the top of the card.
    """
    entry: dict[str, Any] = {"Front": front, "Back": back}
    if arete_id:
        entry["id"] = arete_id
    if model:
        entry["model"] = model
    if deck:
        entry["deck"] = deck
    if tags:
        entry["tags"] = list(tags)
    if requires is not None:
        entry["deps"] = {"requires": list(requires), "related": []}
    if nid is not None or cid is not None:
        anki: dict[str, str] = {}
        if nid is not None:
            anki["nid"] = str(nid)
        if cid is not None:
            anki["cid"] = str(cid)
        entry["anki"] = anki
    if extra:
        entry.update(extra)
    return entry


def note_markdown(
    *,
    cards: list[dict[str, Any]] | None = None,
    deck: str = DEFAULT_DECK,
    model: str = DEFAULT_MODEL,
    tags: list[str] | None = None,
    body: str = "Body text.",
) -> str:
    """A whole vault note: frontmatter plus body, as Arete would find it on disk."""
    import yaml

    meta: dict[str, Any] = {"arete": True, "deck": deck, "model": model}
    if tags:
        meta["tags"] = list(tags)
    meta["cards"] = cards if cards is not None else [card()]
    dumped = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).rstrip()
    return f"---\n{dumped}\n---\n\n{body}\n"


def write_note(directory: Path, name: str = "Concept.md", **kwargs: Any) -> Path:
    """Write `note_markdown(**kwargs)` into `directory` and return the path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(note_markdown(**kwargs), encoding="utf-8")
    return path


def anki_note(
    *,
    front: str = "What is a port?",
    back: str = "What a caller needs, not how it is done.",
    deck: str = DEFAULT_DECK,
    model: str = DEFAULT_MODEL,
    tags: list[str] | None = None,
    nid: str | None = None,
    cid: str | None = None,
    source_file: Path | None = None,
    source_index: int = 1,
    content_hash: str | None = None,
) -> AnkiNote:
    """The domain object a parsed card becomes."""
    return AnkiNote(
        model=model,
        deck=deck,
        fields={"Front": front, "Back": back},
        tags=list(tags) if tags else [],
        start_line=1,
        end_line=5,
        source_file=source_file or Path("Concept.md"),
        source_index=source_index,
        nid=nid,
        cid=cid,
        content_hash=content_hash,
    )


def make_config(vault: Path, **overrides: Any) -> AppConfig:
    """A config pointed entirely inside `vault`'s parent, so a test writes nowhere else."""
    tmp = vault.parent
    media = tmp / "media"
    media.mkdir(exist_ok=True)
    defaults: dict[str, Any] = {
        "root_input": vault,
        "vault_root": vault,
        "anki_media_dir": media,
        "anki_base": tmp / "anki_base",
        "backend": "direct",
        "cache_db": str(tmp / "cache.db"),
        "log_dir": tmp / "logs",
        "workers": 2,
        "prune": False,
    }
    defaults.update(overrides)
    return AppConfig(**defaults)
