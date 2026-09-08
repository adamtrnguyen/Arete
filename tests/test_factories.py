"""The factories must agree with the product, or they are worse than no factory.

A shared builder that drifts is a single point of failure instead of a single source
of truth. These tests feed `tests/factories.py` output through Arete's own parser and
compare against what Arete writes back, so a format change fails here first.
"""

import logging
from pathlib import Path

from arete.application.sync.parser import MarkdownParser
from arete.application.sync.vault_service import VaultService
from arete.application.utils.media import build_filename_index
from arete.application.utils.text import parse_frontmatter
from arete.infrastructure.persistence.cache import ContentCache
from tests import factories


def _parse(vault: Path, tmp_path: Path):
    log = logging.getLogger("factories")
    cache = ContentCache(db_path=tmp_path / "factory-cache.db")
    service = VaultService(vault, cache, ignore_cache=True)
    parser = MarkdownParser(
        vault, tmp_path / "media", ignore_cache=True, default_deck="Default", logger=log
    )
    index = build_filename_index(vault, log)
    out = []
    for path, meta, _fresh in service.scan_for_compatible_files():
        notes, _skipped, _inv = parser.parse_file(path, meta, cache, index, False)
        out.extend(notes)
    return out


def test_a_generated_note_is_one_arete_accepts(tmp_path: Path):
    vault = tmp_path / "vault"
    factories.write_note(vault, "Concept.md", deck="AI::Deep Learning")

    notes = _parse(vault, tmp_path)

    assert len(notes) == 1
    assert notes[0].deck == "AI::Deep Learning"
    assert "What is a port?" in notes[0].fields["Front"], "fields are rendered to HTML"
    assert notes[0].content_hash, "a parsed card must carry a content hash"


def test_the_factory_puts_the_anki_ids_where_arete_reads_them(tmp_path: Path):
    """The nesting is the whole point.

    Three plugin test files each wrote a flat `nid:` at the top of the card. Arete
    writes `anki: {nid: ...}` and reads nothing else, so those fixtures described a
    card the product never produces.
    """
    vault = tmp_path / "vault"
    factories.write_note(
        vault, "Concept.md", cards=[factories.card(nid=1771190868878, cid=1771190868878)]
    )

    raw = (vault / "Concept.md").read_text(encoding="utf-8")
    meta, _body = parse_frontmatter(raw)
    entry = meta["cards"][0]
    assert "nid" not in entry, "a bare nid at card level is not the format Arete writes"
    assert entry["anki"]["nid"] == "1771190868878"

    notes = _parse(vault, tmp_path)
    assert notes[0].nid == "1771190868878", "the parser must pick the id up from anki.nid"


def test_multiple_cards_and_dependencies_survive_a_round_trip(tmp_path: Path):
    vault = tmp_path / "vault"
    factories.write_note(
        vault,
        "Concept.md",
        cards=[
            factories.card(front="First", arete_id="arete_01TESTCARD0000000000000001"),
            factories.card(
                front="Second",
                arete_id="arete_01TESTCARD0000000000000002",
                requires=["arete_01TESTCARD0000000000000001"],
            ),
        ],
    )

    notes = _parse(vault, tmp_path)

    fronts = [n.fields["Front"] for n in notes]
    assert len(fronts) == 2
    assert "First" in fronts[0] and "Second" in fronts[1]
    assert {t for n in notes for t in n.tags if t.startswith("arete_")} == {
        "arete_01TESTCARD0000000000000001",
        "arete_01TESTCARD0000000000000002",
    }


def test_a_card_level_deck_beats_the_file_deck(tmp_path: Path):
    vault = tmp_path / "vault"
    factories.write_note(
        vault,
        "Concept.md",
        deck="File::Deck",
        cards=[factories.card(front="A"), factories.card(front="B", deck="Card::Deck")],
    )

    decks = {("A" if "A" in n.fields["Front"] else "B"): n.deck for n in _parse(vault, tmp_path)}

    assert decks == {"A": "File::Deck", "B": "Card::Deck"}
