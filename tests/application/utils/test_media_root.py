import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from arete.application.utils.media import (
    _resolve_candidate_paths,
    build_filename_index,
    transform_images_in_text,
    unique_media_name,
)


@pytest.fixture
def mock_logger():
    return MagicMock(spec=logging.Logger)


def test_unique_media_name_no_collision(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    src = tmp_path / "image.png"
    src.write_text("content")

    name = unique_media_name(dest, src)
    assert name == "image.png"


def test_unique_media_name_same_content(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()

    src = tmp_path / "image.png"
    src.write_bytes(b"content")

    # file exists in dest with SAME content
    existing = dest / "image.png"
    existing.write_bytes(b"content")

    name = unique_media_name(dest, src)
    assert name == "image.png"  # Should reuse name if content matches


def test_unique_media_name_diff_content(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()

    src = tmp_path / "image.png"
    src.write_bytes(b"new content")

    # file exists in dest with DIFF content
    existing = dest / "image.png"
    existing.write_bytes(b"old content")

    name = unique_media_name(dest, src)
    assert name != "image.png"
    assert name.startswith("image_")
    assert name.endswith(".png")


def test_build_filename_index_nested_dirs(tmp_path, mock_logger):
    vault = tmp_path / "Vault"
    vault.mkdir()
    (vault / "assets").mkdir()
    (vault / "assets/sub").mkdir()

    (vault / "assets/a.png").touch()
    (vault / "assets/sub/b.jpg").touch()
    (vault / "ignore.txt").touch()  # Not in a media folder

    idx = build_filename_index(vault, mock_logger)

    assert "a.png" in idx
    assert "b.jpg" in idx
    assert len(idx["a.png"]) == 1
    assert idx["a.png"][0].name == "a.png"


def test_resolve_candidate_paths_sibling_and_index(tmp_path):
    vault = tmp_path / "Vault"
    md_path = vault / "Notes/note.md"

    # Case 1: Relative path
    res = _resolve_candidate_paths(md_path, vault, "img.png")
    # Should check sibling and vault root
    assert (vault / "Notes/img.png").resolve() in res
    assert (vault / "img.png").resolve() in res

    # Case 2: Using index
    idx = {"unique.png": [Path("/idx/unique.png")]}
    res = _resolve_candidate_paths(md_path, vault, "unique.png", name_index=idx)
    assert Path("/idx/unique.png") in res


def test_transform_images(tmp_path, mock_logger):
    # Setup
    vault = tmp_path / "Vault"
    anki_media = tmp_path / "AnkiMedia"
    anki_media.mkdir()

    md_path = vault / "note.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)

    # Create a real image to be found
    img = vault / "my_image.png"
    img.write_bytes(b"fake image data")

    text = "Look at ![[my_image.png]] here."

    # Run
    out = transform_images_in_text(text, md_path, vault, anki_media, mock_logger)

    # Expectation: ![[...]] -> ![](...) and file copied
    assert "![](" in out
    assert ".png)" in out
    # File should serve form AnkiMedia
    copied = list(anki_media.glob("*.png"))
    assert len(copied) == 1
    assert copied[0].name == "my_image.png"


def test_unicode_filenames(tmp_path):
    vault = tmp_path / "Vault"
    vault.mkdir()
    media_dir = tmp_path / "Media"
    media_dir.mkdir()

    # Create file with emoji
    img_name = "image_🚀.png"
    (vault / img_name).write_bytes(b"space")

    md_path = vault / "note.md"
    text = f"Link: ![[{img_name}]]"

    logger = MagicMock()

    out = transform_images_in_text(text, md_path, vault, media_dir, logger)

    # Should resolve and copy
    # The output format for Anki is ![](filename)
    assert f"![]({img_name})" in out
    assert (media_dir / img_name).exists()


def test_url_encoded_filename(tmp_path):
    vault = tmp_path / "Vault"
    vault.mkdir()
    media_dir = tmp_path / "Media"
    media_dir.mkdir()

    # File on disk has spaces
    img_name = "my cool image.png"
    (vault / img_name).write_bytes(b"cool")

    md_path = vault / "note.md"
    # Obsidian might write as %20
    text = "Link: ![[my%20cool%20image.png]]"

    logger = MagicMock()

    out = transform_images_in_text(text, md_path, vault, media_dir, logger)

    # Should decode %20 -> space and find the file
    assert "my cool image.png" in out
    assert (media_dir / "my cool image.png").exists()


def test_deeply_nested_image_with_index(tmp_path):
    vault = tmp_path / "Vault"
    vault.mkdir()
    media_dir = tmp_path / "Media"
    media_dir.mkdir()

    # Deep nest
    deep_dir = vault / "a/b/c/d/e"
    deep_dir.mkdir(parents=True)
    (deep_dir / "deep.png").write_bytes(b"deep")

    md_path = vault / "note.md"
    text = "![[deep.png]]"

    logger = MagicMock()

    # Without index, it won't find it (unless implicitly checking every folder)
    # So we provide the index
    idx = {"deep.png": [deep_dir / "deep.png"]}

    out = transform_images_in_text(text, md_path, vault, media_dir, logger, name_index=idx)

    assert "deep.png" in out
    assert (media_dir / "deep.png").exists()
