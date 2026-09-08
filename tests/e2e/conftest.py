"""Fixtures for the hermetic end-to-end suite.

These tests drive the real orchestrator against a real Anki collection created in
`tmp_path`. They need no Docker container, no network, and no running Anki, so they
run in CI and on a laptop. The collection is the genuine article: the `anki` library
writes the same SQLite file the desktop app opens.
"""

import pickle
import sqlite3
from pathlib import Path

import pytest
from anki.collection import Collection

from arete.application.config import AppConfig

PROFILE = "User 1"


def make_anki_base(base: Path) -> Path:
    """Create an Anki base directory the direct backend can open.

    `AnkiRepository` resolves the collection through `prefs21.db`, so the profile
    record has to exist before the adapter runs.
    """
    col_dir = base / PROFILE
    col_dir.mkdir(parents=True, exist_ok=True)

    prefs_db = base / "prefs21.db"
    conn = sqlite3.connect(prefs_db)
    conn.execute("CREATE TABLE profiles (name TEXT PRIMARY KEY, data BLOB)")
    conn.execute(
        "INSERT INTO profiles VALUES ('_global', ?)",
        (sqlite3.Binary(pickle.dumps({"last_loaded_profile_name": PROFILE})),),
    )
    conn.commit()
    conn.close()

    # Creating the Collection builds the standard tables and the stock note types.
    Collection(str(col_dir / "collection.anki2")).close()
    return base


def open_collection(base: Path) -> Collection:
    """Open the collection for assertions. The caller closes it."""
    return Collection(str(base / PROFILE / "collection.anki2"))


def deck_of(col: Collection, nid: int) -> str:
    """Return the deck name of a note's first card."""
    card = col.get_card(col.card_ids_of_note(nid)[0])
    deck = col.decks.get(card.did)
    return deck["name"] if deck else "Unknown"


@pytest.fixture
def anki_base(tmp_path: Path) -> Path:
    return make_anki_base(tmp_path / "anki_base")


@pytest.fixture
def single_card_vault(tmp_path: Path) -> Path:
    """The smallest real vault: one file, one card, a nested deck."""
    v = tmp_path / "vault"
    v.mkdir()
    (v / "Transformer.md").write_text(
        "---\n"
        "arete: true\n"
        "deck: AI::Deep Learning::Transformers\n"
        "model: Basic\n"
        "cards:\n"
        "- Front: What are the core components of a Transformer block?\n"
        "  Back: Multi-head self-attention and a feed-forward MLP, each in a residual.\n"
        "---\n\nBody text.\n",
        encoding="utf-8",
    )
    return v


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A two-note vault: one card in a nested deck, two in another."""
    v = tmp_path / "vault"
    v.mkdir()
    (v / "Transformer.md").write_text(
        "---\n"
        "arete: true\n"
        "deck: AI::Deep Learning::Transformers\n"
        "model: Basic\n"
        "tags:\n"
        "- deep_learning\n"
        "cards:\n"
        "- Front: What are the core components of a Transformer block?\n"
        "  Back: Multi-head self-attention and a feed-forward MLP, each in a residual.\n"
        "---\n\nBody text.\n",
        encoding="utf-8",
    )
    (v / "Bayes.md").write_text(
        "---\n"
        "arete: true\n"
        "deck: Statistics::Inference\n"
        "model: Basic\n"
        "cards:\n"
        "- Front: State Bayes' rule.\n"
        "  Back: P(A|B) = P(B|A)P(A)/P(B)\n"
        "- Front: What is a conjugate prior?\n"
        "  Back: A prior whose posterior stays in the same family.\n"
        "---\n\nBody text.\n",
        encoding="utf-8",
    )
    return v


@pytest.fixture
def make_config(anki_base: Path, tmp_path: Path):
    """Build a direct-backend config for a given vault, entirely inside tmp_path."""

    def _make(vault_path: Path, **overrides) -> AppConfig:
        media = tmp_path / "media"
        media.mkdir(exist_ok=True)
        return AppConfig(
            root_input=vault_path,
            vault_root=vault_path,
            anki_media_dir=media,
            anki_base=anki_base,
            backend="direct",
            cache_db=str(tmp_path / "cache.db"),
            log_dir=tmp_path / "logs",
            workers=2,
            prune=False,
            **overrides,
        )

    return _make
