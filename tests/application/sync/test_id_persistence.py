from arete.application.sync.vault_service import VaultService
from arete.domain.models import UpdateItem
from arete.infrastructure.persistence.cache import ContentCache


def test_nid_persistence(tmp_path):
    """Verify that VaultService.apply_updates writes new NIDs back to the markdown file."""
    # 1. Setup: Create a dummy markdown file
    md_file = tmp_path / "test_card.md"
    md_content = """---
arete: true
deck: Default
cards:
  - model: Basic
    Front: Question
    Back: Answer
---
# Content body
"""
    md_file.write_text(md_content, encoding="utf-8")

    # 2. Initialize Service with dummy cache
    # We use a real cache instance but with a tmp db path to ensure no side effects
    cache_db = tmp_path / "test_cache.db"
    cache = ContentCache(db_path=cache_db)

    service = VaultService(tmp_path, cache)

    # 3. Create an UpdateItem simulating a successful Anki sync
    # source_index=1 corresponds to the first card in the list
    update = UpdateItem(
        source_file=md_file, source_index=1, new_nid="123456789", new_cid="987654321", ok=True
    )

    # 4. Action: Apply updates
    service.apply_updates([update])

    # 5. Verification: Read file and check for inserted IDs
    new_content = md_file.read_text(encoding="utf-8")

    print("\n--- File Content After Update ---")
    print(new_content)
    print("---------------------------------")

    # Yaml dump might quote the numbers
    assert "nid: '123456789'" in new_content or "nid: 123456789" in new_content, (
        "NID was not written to file"
    )
    assert "cid: '987654321'" in new_content or "cid: 987654321" in new_content, (
        "CID was not written to file"
    )

    # Also check that structure is preserved (basic check)
    assert "deck: Default" in new_content
    assert "model: Basic" in new_content


def test_existing_nid_update(tmp_path):
    """Verify that if an NID changes (e.g. healing), it updates the existing field."""
    md_file = tmp_path / "test_update.md"
    md_content = """---
arete: true
cards:
  - model: Basic
    anki:
      nid: '11111'
    Front: Q
    Back: A
---
"""
    md_file.write_text(md_content, encoding="utf-8")

    cache_db = tmp_path / "test_cache_2.db"
    cache = ContentCache(db_path=cache_db)
    service = VaultService(tmp_path, cache)

    # Simulate healing where NID changes from 11111 to 22222
    update = UpdateItem(
        source_file=md_file,
        source_index=1,
        new_nid="22222",
        new_cid=None,  # unchanged
        ok=True,
    )

    service.apply_updates([update])

    new_content = md_file.read_text(encoding="utf-8")
    assert "nid: '22222'" in new_content or "nid: 22222" in new_content
    assert "nid: 11111" not in new_content
    assert "anki:" in new_content
