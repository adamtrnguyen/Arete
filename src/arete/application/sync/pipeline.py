import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from tqdm import tqdm

from arete.application.config import AppConfig
from arete.application.sync.id_service import ensure_card_ids
from arete.application.sync.parser import MarkdownParser
from arete.application.sync.prune import PruneConfirm, PruneReport, prune_orphans
from arete.application.sync.vault_service import VaultService
from arete.application.utils.logging import RunRecorder
from arete.application.utils.media import FilenameIndex
from arete.application.utils.text import rebuild_markdown_with_frontmatter
from arete.domain.constants import CONSUMER_BATCH_SIZE
from arete.domain.interfaces import ContentCache, SyncPort
from arete.domain.models import UpdateItem, WorkItem


@runtime_checkable
class _BatchingCache(Protocol):
    def set_notes(self, rows: list[tuple[Path, int, str, str]]) -> None: ...


def _record_synced_cards(cache: ContentCache, rows: list[tuple[Path, int, str, str]]) -> None:
    """Record cards Anki has accepted, one transaction per consumer batch.

    Called only after the bridge returned, with ok rows only. A crash before the commit
    loses the rows, so those cards are checked again next sync; nothing is recorded early.
    """
    if not rows:
        return
    if isinstance(cache, _BatchingCache):
        cache.set_notes(rows)
        return
    for row in rows:
        cache.set_note(*row)


@dataclass
class RunStats:
    total_generated: int
    total_imported: int
    total_errors: int
    prune: PruneReport | None = field(default=None)


async def run_pipeline(
    config: AppConfig,
    logger: logging.Logger,
    run_id: str,
    vault_service: VaultService,
    parser: MarkdownParser,
    anki_bridge: SyncPort,
    cache: ContentCache,
    *,
    confirm_prune: bool = False,
    prompt: PruneConfirm | None = None,
) -> RunStats:
    """Sync the vault to Anki, then prune if asked.

    `confirm_prune` is the caller's explicit consent to delete. Without it prune asks
    `prompt` at a terminal, and anywhere else returns a preview and deletes nothing.
    """
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
        return RunStats(0, 0, 0)

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
                _, body = vault_service.parse(path, text)
                new_text = rebuild_markdown_with_frontmatter(meta, body)
                if config.dry_run:
                    logger.info(f"[dry-run] would assign {assigned} new IDs in {path.name}")
                else:
                    path.write_text(new_text, encoding="utf-8")
                    logger.info(f"[id] Assigned {assigned} new IDs in {path.name}")
                # Re-parse so downstream stages see the updated meta
                new_meta, _ = vault_service.parse(path, new_text)
                updated_compatible.append((path, new_meta, True))
            except Exception as e:
                logger.error(f"[id-error] Failed to rewrite {path}: {e}")
                updated_compatible.append((path, meta, is_fresh))
        else:
            updated_compatible.append((path, meta, is_fresh))
    compatible = updated_compatible
    if ids_total > 0:
        logger.info(f"[id] Assigned {ids_total} new Arete IDs across vault")

    failed_files: set[Path] = set()  # a card here did not reach Anki; do not mark it synced

    # -------- Stage 1.6: one Anki note claimed by cards in different files --------
    claims: dict[str, list[tuple[Path, int]]] = {}
    for path, meta, _fresh in compatible:
        for idx, card in enumerate(meta.get("cards") or [], start=1):
            if isinstance(card, dict) and (nid := parser._extract_raw_nid(card)):
                claims.setdefault(nid, []).append((path, idx))
    contested = {nid: c for nid, c in claims.items() if len({p for p, _ in c}) > 1}
    parser.contested_nids = {
        nid: [f"{p.name} card#{i}" for p, i in c] for nid, c in contested.items()
    }
    for nid, c in contested.items():
        for p, i in c:
            failed_files.add(p)
            recorder.add_error(p, f"anki.nid {nid} is claimed by cards in several files", f"#{i}")

    # -------- Stage 2: media index, walked on the first image lookup --------
    assert config.vault_root is not None  # Guaranteed by resolve_config
    name_index = FilenameIndex(config.vault_root, logger)

    # -------- Stage 3: Producers + Consumer --------
    work_q: asyncio.Queue[WorkItem | None] = asyncio.Queue(maxsize=max(1, config.queue_size))
    updates: list[UpdateItem] = []
    updates_lock = asyncio.Lock()
    unparsed_files: list[Path] = []

    # How many batches may be in flight. A backend that cannot take overlapping
    # calls serializes them itself, so this does not depend on which one we got.
    max_sync_concurrency = max(1, config.workers)
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
            unparsed_files.append(md_file)  # its nids never reached the prune inventory
            failed_files.add(md_file)

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
                if config.dry_run:
                    # Dry run: never touch Anki. Report what WOULD happen and keep the
                    # cache untouched so the next real run still sees these cards as pending.
                    batch_updates = []
                    for wi in batch:
                        action = "update" if wi.note.nid else "create"
                        logger.info(
                            f"[dry-run] would {action} {wi.source_file.name} "
                            f"#{wi.source_index} deck={wi.note.deck!r}"
                        )
                        batch_updates.append(
                            UpdateItem(
                                source_file=wi.source_file,
                                source_index=wi.source_index,
                                new_nid=wi.note.nid,
                                new_cid=wi.note.cid,
                                ok=True,
                                note=wi.note,
                            )
                        )
                else:
                    async with sync_semaphore:
                        batch_updates = await anki_bridge.sync_notes(batch)

                async with updates_lock:
                    synced_rows: list[tuple[Path, int, str, str]] = []
                    for u in batch_updates:
                        updates.append(u)
                        if u.ok:
                            recorder.cards_synced += 1
                            # A note created this run is in the vault now: protect it from
                            # --prune, which otherwise saw its fresh nid as an orphan.
                            if u.new_nid:
                                recorder.add_inventory(
                                    [{"nid": u.new_nid, "deck": u.note.deck if u.note else None}]
                                )
                            if u.note and u.note.content_hash and not config.dry_run:
                                # Refresh the hot-cache entry WITH the nid Anki assigned,
                                # otherwise the cached note keeps nid=None and every warm
                                # run re-sends the card as new.
                                u.note.nid = u.new_nid or u.note.nid
                                u.note.cid = u.new_cid or u.note.cid
                                synced_rows.append(
                                    (
                                        u.source_file,
                                        u.source_index,
                                        u.note.content_hash,
                                        json.dumps(u.note.to_dict()),
                                    )
                                )
                        else:
                            recorder.cards_failed += 1
                            failed_files.add(u.source_file)
                            recorder.add_error(
                                u.source_file, f"Sync fail: {u.error}", f"#{u.source_index}"
                            )
                    _record_synced_cards(cache, synced_rows)
            except Exception as e:
                logger.error(f"[consumer-error] {e}")
                for wi in batch:
                    failed_files.add(wi.source_file)
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

    # disable=None: tqdm shows the bar only when stderr is a terminal.
    with tqdm(total=len(producer_tasks), desc="Processing", unit="file", disable=None) as pbar:
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

    # A file is recorded as synced only once every card in it reached Anki, so a dry
    # run or a failed card leaves it fresh and the next run looks at it again.
    if not config.dry_run:
        vault_service.record_synced(
            p for p, _meta, is_fresh in compatible if is_fresh and p not in failed_files
        )

    # -------- Stage 5: Prune Orphans (Destructive) --------
    prune_report: PruneReport | None = None
    if config.prune:
        blockers = [f"{p.name} ({why})" for p, why in vault_service.unreadable]
        blockers += [f"{p.name} (parse crashed)" for p in unparsed_files]
        if blockers:
            msg = (
                "these Arete files could not be read, so their notes cannot be told apart "
                f"from orphans. Fix them and prune again: {', '.join(blockers)}"
            )
            logger.warning(f"[prune] REFUSED: {msg}")
            prune_report = PruneReport("refused", msg)
        else:
            prune_report = await prune_orphans(
                config, recorder, anki_bridge, logger, confirmed=confirm_prune, prompt=prompt
            )

    total_generated = len(updates)
    total_imported = sum(1 for u in updates if u.ok)
    total_errors = len(recorder.errors)

    return RunStats(
        total_generated=total_generated,
        total_imported=total_imported,
        total_errors=total_errors,
        prune=prune_report,
    )
