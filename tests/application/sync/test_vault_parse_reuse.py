"""One sync parses each version of a file's text once, and writes the same bytes as before."""

from __future__ import annotations

from pathlib import Path

import pytest

from arete.application.sync import pipeline, vault_service
from arete.application.sync.vault_service import VaultService
from arete.application.utils.text import parse_frontmatter
from arete.infrastructure.persistence.cache import ContentCache
from tests.application.sync.test_pipeline_cache_batching import CheckingAnki, sync

NO_IDS = "---\narete: true\ndeck: D\ncards:\n- Front: q1\n  Back: a1\n- Front: q2\n  Back: a2\n---\nbody\n"


@pytest.fixture
def parses(monkeypatch):
    """Record every text parsed, wherever in the sync it is parsed."""
    seen: list[str] = []

    def counting(text):
        seen.append(text)
        return parse_frontmatter(text)

    monkeypatch.setattr(vault_service, "parse_frontmatter", counting)
    monkeypatch.setattr(pipeline, "parse_frontmatter", counting, raising=False)
    return seen


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = (tmp_path / "vault").resolve()
    r.mkdir()
    return r


def _write(md: Path, text: str, eol: str) -> None:
    """Store `text` with `eol` line endings, byte for byte on every OS."""
    md.write_bytes(text.replace("\n", eol).encode("utf-8"))


@pytest.mark.parametrize("eol", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_a_new_file_is_parsed_once_per_text_it_has(root: Path, tmp_path: Path, parses, eol):
    """Read, ids added, nids written back: three texts, three parses, whatever the line ends."""
    md = root / "n.md"
    _write(md, NO_IDS, eol)
    cache = ContentCache(tmp_path / "c.db")
    sync(root, CheckingAnki(cache), cache)

    final = md.read_text(encoding="utf-8")
    assert "nid:" in final and "id: arete_" in final
    assert (b"\r\n" in md.read_bytes()) == (eol == "\r\n"), "the line endings are kept"
    assert len(parses) == 3
    assert len(set(parses)) == 3, "no text was parsed twice"
    assert parses[0] == NO_IDS and parses[-1] == final


@pytest.mark.parametrize("eol", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_an_edited_file_with_known_ids_is_parsed_once(root: Path, tmp_path: Path, parses, eol):
    md = root / "n.md"
    _write(md, NO_IDS, eol)
    cache = ContentCache(tmp_path / "c.db")
    anki = CheckingAnki(cache)
    sync(root, anki, cache)

    _write(md, md.read_text(encoding="utf-8").replace("a2", "a2 edited"), eol)
    parses.clear()
    sync(root, anki, cache)
    assert len(anki.sent) == 4, "both cards went again: their hash now covers the nid"
    assert len(parses) == 1, "scan, write-back check and record_synced share one parse"


def test_a_reused_parse_is_a_copy_the_caller_may_change(tmp_path: Path):
    service = VaultService(tmp_path, ContentCache(tmp_path / "c.db"))
    md = tmp_path / "n.md"
    first, body = service.parse(md, NO_IDS)
    first["cards"][0]["anki"] = {"nid": "1"}
    second, body2 = service.parse(md, NO_IDS)
    assert second == parse_frontmatter(NO_IDS)[0]
    assert body2 == body == "body\n"
    second["deck"] = "changed"
    assert service.parse(md, NO_IDS)[0]["deck"] == "D"


def test_a_different_text_is_parsed_again(tmp_path: Path):
    service = VaultService(tmp_path, ContentCache(tmp_path / "c.db"))
    md = tmp_path / "n.md"
    service.parse(md, NO_IDS)
    other = NO_IDS.replace("deck: D", "deck: E")
    assert service.parse(md, other)[0]["deck"] == "E"
