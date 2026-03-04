import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from tqdm import tqdm  # type: ignore
except ImportError:
    tqdm = None

from arete.application.config import AppConfig
from arete.application.sync.id_service import ensure_card_ids
from arete.application.sync.parser import MarkdownParser
from arete.application.utils.logging import RunRecorder
from arete.application.utils.media import build_filename_index
from arete.application.utils.text import parse_frontmatter, rebuild_markdown_with_frontmatter
from arete.application.sync.vault_service import VaultService
from arete.domain.constants import CONSUMER_BATCH_SIZE
from arete.domain.interfaces import AnkiBridge
from arete.domain.models import AnkiDeck, UpdateItem, WorkItem
from arete.domain.interfaces import ContentCache


@dataclass
class RunStats:
    total_generated: int
    total_imported: int
    total_errors: int
    errors: list[UpdateItem]


async def run_pipeline(
    config: AppConfig,
    logger: logging.Logger,
    run_id: str,
    vault_service: VaultService,
    parser: MarkdownParser,
    anki_bridge: AnkiBridge,
    cache: ContentCache,
) -> RunStats:
    # Use tqdm if interactive and imported
    use_tqdm = "tqdm" in sys.modules

    recorder = RunRecorder()

    # -------- Stage 1: filter --------
    logger.info("[pipeline] Scanning vault...")
    compatible: list[tuple[Path, dict[str, Any], bool]] = list(
        vault_service.scan_for_compatible_files()
    )

    logger.info(f"[filter] compatible files: {len(compatible)}")
    logger.debug("Compatible files:")
    for c in compatible:
        logger.debug(f"  - {c[0]} (fresh={c[2]})")
    if not compatible:
        logger.info("No compatible markdown files found.")
        return RunStats(0, 0, 0, [])

    # -------- Stage 1.5: ensure all cards have Arete IDs --------
    ids_total = 0
    updated_compatible: list[tuple[Path, dict[str, Any], bool]] = []
    for path, meta, is_fresh in compatible:
        assigned = ensure_card_ids(meta)
        if assigned > 0:
            ids_total += assigned
            # Rewrite the file so the parser sees the new id: fields
            try:
                text = path.read_text(encoding="utf-8")
                _, body = parse_frontmatter(text)
                new_text = rebuild_markdown_with_frontmatter(meta, body)
                path.write_text(new_text, encoding="utf-8")
                logger.info(f"[id] Assigned {assigned} new IDs in {path.name}")
                # Re-parse so downstream stages see the updated meta
                new_meta, _ = parse_frontmatter(new_text)
                updated_compatible.append((path, new_meta, True))
            except Exception as e:
                logger.error(f"[id-error] Failed to rewrite {path}: {e}")
                updated_compatible.append((path, meta, is_fresh))
        else:
            updated_compatible.append((path, meta, is_fresh))
    compatible = updated_compatible
    if ids_total > 0:
        logger.info(f"[id] Assigned {ids_total} new Arete IDs across vault")

    # -------- Stage 2: build media index --------
    assert config.vault_root is not None  # Guaranteed by resolve_config
    name_index = build_filename_index(config.vault_root, logger)

    # -------- Stage 3: Producers + Consumer --------
    work_q: asyncio.Queue[WorkItem | None] = asyncio.Queue(maxsize=max(1, config.queue_size))
    updates: list[UpdateItem] = []
    updates_lock = asyncio.Lock()

    # Concurrency control:
    # AnkiConnect can handle multiple requests, but apy (SQLite/CLI) should be sequential.

    max_sync_concurrency = 1 if anki_bridge.is_sequential else max(1, config.workers)
    sync_semaphore = asyncio.Semaphore(max_sync_concurrency)

    async def producer_file(md_file: Path, meta: dict[str, Any], is_fresh: bool):
        recorder.files_scanned += 1
        try:
            notes, skipped_indices, inventory = parser.parse_file(
                md_file, meta, cache, name_index, is_fresh
            )
            recorder.cards_generated += len(notes) + len(skipped_indices)
            recorder.cards_cached_content += len(inventory) - len(notes)

            recorder.add_inventory(inventory)

            for note in notes:
                wi = WorkItem(note=note, source_file=md_file, source_index=note.source_index)
                await work_q.put(wi)
        except Exception as e:
            logger.error(f"[producer-error] {md_file}: {e}")
            recorder.add_error(md_file, str(e))

    async def consumer():
        while True:
            # Wait for at least one item
            first_item = await work_q.get()
            if first_item is None:
                work_q.task_done()
                break

            batch = [first_item]

            # Non-blocking peek for more items to batch them
            while len(batch) < CONSUMER_BATCH_SIZE:
                try:
                    next_item = work_q.get_nowait()
                    if next_item is None:
                        # Re-queue sentinel for other consumers
                        await work_q.put(None)
                        break
                    batch.append(next_item)
                except asyncio.QueueEmpty:
                    break

            try:
                async with sync_semaphore:
                    batch_updates = await anki_bridge.sync_notes(batch)

                async with updates_lock:
                    for u in batch_updates:
                        updates.append(u)
                        if u.ok:
                            recorder.cards_synced += 1
                            if u.note and u.note.content_hash:
                                cache.set_hash(u.source_file, u.source_index, u.note.content_hash)
                        else:
                            recorder.cards_failed += 1
                            recorder.add_error(
                                u.source_file, f"Sync fail: {u.error}", f"#{u.source_index}"
                            )
            except Exception as e:
                logger.error(f"[consumer-error] {e}")
                for wi in batch:
                    recorder.add_error(
                        wi.source_file, f"Consumer batch crash: {e}", f"#{wi.source_index}"
                    )
            finally:
                for _ in range(len(batch)):
                    work_q.task_done()

    # Create consumers
    consumers = [asyncio.create_task(consumer()) for _ in range(max_sync_concurrency)]

    # Run producers
    # Limit producer concurrency as well to avoid overwhelming memory if vault is huge
    prod_semaphore = asyncio.Semaphore(max(1, config.workers))

    async def bounded_producer(p, m, f):
        async with prod_semaphore:
            await producer_file(p, m, f)

    producer_tasks = [
        asyncio.create_task(bounded_producer(p, meta, is_fresh))
        for (p, meta, is_fresh) in compatible
    ]

    if use_tqdm and tqdm:
        with tqdm(total=len(producer_tasks), desc="Processing", unit="file") as pbar:
            for coro in asyncio.as_completed(producer_tasks):
                await coro
                pbar.update(1)
                pbar.set_postfix(
                    {
                        "gen": recorder.cards_generated,
                        "ok": recorder.cards_synced,
                        "err": recorder.cards_failed,
                    }
                )
    else:
        await asyncio.gather(*producer_tasks)

    # Signal consumers to stop
    for _ in range(max_sync_concurrency):
        await work_q.put(None)

    # Wait for consumers to finish processing and exit
    # NOTE: Do NOT use work_q.join() here. When a consumer encounters a None
    # sentinel during batch building (get_nowait), it re-queues the None via
    # put(), which increments unfinished_tasks without a matching task_done().
    # This causes join() to hang indefinitely. gather() simply waits for all
    # consumer coroutines to return.
    await asyncio.gather(*consumers)

    # -------- Stage 4: Persist Updates (Write back NIDs) --------
    if updates:
        logger.info("[pipeline] Persisting NIDs/CIDs to frontmatter...")
        vault_service.apply_updates(updates, dry_run=config.dry_run)

    # -------- Stage 5: Prune Orphans (Destructive) --------
    if config.prune:
        await _prune_orphans(config, recorder, anki_bridge, logger)

    total_generated = len(updates)
    total_imported = sum(1 for u in updates if u.ok)
    total_errors = len(recorder.errors)
    error_items = [u for u in updates if not u.ok]

    return RunStats(
        total_generated=total_generated,
        total_imported=total_imported,
        total_errors=total_errors,
        errors=error_items,
    )


async def _prune_orphans(
    config: AppConfig, recorder: RunRecorder, bridge: AnkiBridge, logger: logging.Logger
):
    if config.root_input != config.vault_root:
        logger.warning(
            "[prune] SKIPPED: Pruning requires running on the entire vault root to ensure safety."
        )
        return

    logger.info("[prune] analyzing vault vs anki state...")

    valid_nids = recorder.inventory_nids
    valid_decks = recorder.inventory_decks

    logger.debug(f"[prune] Found {len(valid_nids)} valid NIDs in inventory.")
    logger.debug(f"[prune] Found {len(valid_decks)} valid decks in inventory.")

    anki_decks = await bridge.get_deck_names()

    protected_decks = set(valid_decks)
    for d in valid_decks:
        deck_obj = AnkiDeck(name=d)
        protected_decks.update(deck_obj.parents)

    orphan_decks = []
    for d in anki_decks:
        if d not in protected_decks and d != "Default":
            orphan_decks.append(d)

    decks_to_scan = set(anki_decks) - {"Default"}
    if not decks_to_scan:
        logger.info("[prune] No non-default decks found.")
        return

    orphan_note_ids = []

    for d in decks_to_scan:
        if d in orphan_decks:
            continue

        deck_notes = await bridge.get_notes_in_deck(d)
        for nid_str, anki_id in deck_notes.items():
            if nid_str not in valid_nids:
                orphan_note_ids.append(anki_id)
                logger.debug(f"[prune] Identified orphan: nid={nid_str}, anki_id={anki_id} in {d}")
            else:
                logger.debug(f"[prune] Valid note confirmed: nid={nid_str} in {d}")

    n_decks = len(orphan_decks)
    n_notes = len(orphan_note_ids)

    if n_decks == 0 and n_notes == 0:
        logger.info("[prune] Clean. No orphans found.")
        return

    logger.warning("\n" + "=" * 40)
    logger.warning("PRUNE SUMMARY")
    logger.info(f"Valid NIDs found in vault: {len(valid_nids)}")
    logger.info(f"Valid Decks found in vault: {len(valid_decks)}")
    logger.warning("-" * 20)
    logger.warning(f"Orphan Decks to DELETE: {n_decks}")
    for d in orphan_decks:
        logger.warning(f"  - [DECK] {d}")
    logger.warning(f"Orphan Notes to DELETE: {n_notes}")
    if n_notes > 0:
        logger.warning(f"  - [NOTES] {n_notes} cards will be permanently removed.")

    if not config.force:
        # Use asyncio-friendly input if needed?
        # For CLI, standard input() is okay because the whole bridge is waiting here anyway.
        val = input("\nAre you sure you want to proceed? Type 'yes' to confirm: ")
        if val.lower() != "yes":
            logger.info("[prune] Aborted by user.")
            return

    if config.dry_run:
        logger.warning("[prune] DRY RUN: Destructive actions skipped.")
        return

    if n_notes > 0:
        logger.info(f"[prune] Deleting {n_notes} notes...")
        try:
            await bridge.delete_notes(orphan_note_ids)
        except Exception as e:
            logger.error(f"[prune] Failed to delete notes: {e}")

    if n_decks > 0:
        logger.info(f"[prune] Deleting {n_decks} decks...")
        try:
            await bridge.delete_decks(orphan_decks)
        except Exception as e:
            logger.error(f"[prune] Failed to delete decks: {e}")

    logger.info("[prune] Pruning complete.")
