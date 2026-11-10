import logging
from unittest.mock import patch

import pytest

from arete.application.utils.media import (
    _copy_to_anki_media,
    _media_target,
    _resolve_candidate_paths,
    build_filename_index,
    transform_images_in_text,
)


@pytest.fixture
def logger():
    return logging.getLogger("test_media")


def test_media_target_falls_back_to_a_unique_name_when_files_cannot_be_compared(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    src = tmp_path / "img.png"
    src.write_text("content")

    cand = dest / "img.png"
    cand.touch()

    # Mock exists to return True, but stat to fail
    with patch("pathlib.Path.exists", return_value=True):
        with patch("pathlib.Path.stat", side_effect=Exception("stat error")):
            res, present = _media_target(dest, src)
            assert "img_" in res
            assert not present


def test_copy_to_anki_media_success(tmp_path, logger):
    src = tmp_path / "src.png"
    src.write_text("data")
    dest_dir = tmp_path / "anki_media"

    res = _copy_to_anki_media(src, dest_dir, logger)
    assert res == "src.png"
    assert (dest_dir / "src.png").exists()


def test_copy_to_anki_media_mkdir_exception(tmp_path, logger):
    src = tmp_path / "src.png"
    src.touch()
    dest_dir = tmp_path / "protected"
    dest_dir.touch()  # Make it a file so mkdir fails

    res = _copy_to_anki_media(src, dest_dir, logger)
    assert res is None


def test_copy_to_anki_media_copy_fails(tmp_path, logger):
    src = tmp_path / "src.png"
    src.touch()
    dest_dir = tmp_path / "anki_media"

    with patch("shutil.copy2", side_effect=Exception("copy error")):
        res = _copy_to_anki_media(src, dest_dir, logger)
        assert res is None


def test_build_filename_index(tmp_path, logger):
    vault = tmp_path / "vault"
    vault.mkdir()
    assets = vault / "assets"
    assets.mkdir()
    (assets / "pic.png").write_text("data")

    # Nested
    sub = assets / "sub"
    sub.mkdir()
    (sub / "nested.jpg").write_text("data")

    idx = build_filename_index(vault, logger)
    assert "pic.png" in idx
    assert "nested.jpg" in idx
    assert len(idx["pic.png"]) == 1


def test_resolve_candidate_paths(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    md_path = vault / "note.md"

    # Relative to MD
    (vault / "img.png").touch()

    # name index
    (vault / "assets").mkdir()
    pic = vault / "assets" / "pic.png"
    pic.touch()
    idx = {"pic.png": [pic]}

    # 1. Simple relative
    res = _resolve_candidate_paths(md_path, vault, "img.png")
    assert vault / "img.png" in res

    # 2. With pipe (Obsidian alt text)
    res = _resolve_candidate_paths(md_path, vault, "img.png|100")
    assert vault / "img.png" in res

    # 3. From index
    res = _resolve_candidate_paths(md_path, vault, "pic.png", name_index=idx)
    assert pic in res


def test_transform_images_in_text(tmp_path, logger):
    vault = tmp_path / "vault"
    vault.mkdir()
    md_path = vault / "note.md"
    anki_media = tmp_path / "anki_media"

    img = vault / "img.png"
    img.write_text("data")

    text = "![[img.png]] ![](img.png) ![](https://remote.com/a.png)"
    res = transform_images_in_text(text, md_path, vault, anki_media, logger)

    assert "![](img.png)" in res
    assert "https://remote.com/a.png" in res
    assert (anki_media / "img.png").exists()


def test_transform_markdown_images_missing_logging(tmp_path, logger):
    vault = tmp_path / "vault"
    vault.mkdir()
    md_path = vault / "note.md"
    anki_media = tmp_path / "anki_media"

    text = "![](nonexistent.png)"
    # Should just return original text if not found
    res = transform_images_in_text(text, md_path, vault, anki_media, logger)
    assert res == "![](nonexistent.png)"


def test_transform_images_copy_fails_logging(tmp_path, logger):
    vault = tmp_path / "vault"
    vault.mkdir()
    md_path = vault / "note.md"
    anki_media = tmp_path / "anki_media"

    (vault / "img.png").touch()

    with patch("arete.application.utils.media._copy_to_anki_media", return_value=None):
        with patch.object(logger, "warning") as mock_warn:
            text = "![[img.png]]"
            transform_images_in_text(text, md_path, vault, anki_media, logger)
            found = any(
                "Copy failed" in call.args[0] or "missing file" in call.args[0]
                for call in mock_warn.call_args_list
            )
            assert found
