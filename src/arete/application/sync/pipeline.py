import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tqdm import tqdm

from arete.application.config import AppConfig
from arete.application.sync.note_sync import NoteSync, VaultClaims
from arete.application.sync.parser import MarkdownParser
from arete.application.sync.prune import PruneConfirm, PruneReport, prune_orphans
from arete.application.sync.vault_service import VaultService
from arete.application.utils.common import sanitize
from arete.application.utils.logging import RunRecorder
from arete.application.utils.media import FilenameIndex
from arete.domain.constants import CONSUMER_BATCH_SIZE
from arete.domain.interfaces import ContentCache, SyncBackend
from arete.domain.models import UpdateItem, WorkItem


def vault_claims(
    compatible: list[tuple[Path, dict[str, Any], bool]], parser: MarkdownParser
) -> VaultClaims:
    """Every nid and Arete id the scanned files declare, casefolded, for `NoteSync`."""
    owners: dict[int, set[str | None]] = {}
    ids: set[str] = set()
    for _path, meta, _fresh in compatible:
        for card in meta.get("cards") or []:
            card_id = parser.declared_id(card)
            key = card_id.casefold() if card_id else None
            if key:
                ids.add(key)
            raw = parser._extract_raw_nid(card) if isinstance(card, dict) else None
            if raw and raw.isdigit():
                owners.setdefault(int(raw), set()).add(key)
    return VaultClaims({nid: frozenset(o) for nid, o in owners.items()}, frozenset(ids))


def vault_models(compatible: list[tuple[Path, dict[str, Any], bool]]) -> set[str]:
    """The note types the scanned cards name: a card's own, else its file's, else Basic."""
    models: set[str] = set()
    for _path, meta, _fresh in compatible:
        default = sanitize(meta.get("model", "Basic"))
        for card in meta.get("cards") or []:
            if isinstance(card, dict):
                models.add(sanitize(card.get("model", default)))
    return models


async def true_source_lines(
    written: set[Path],
    updates: list[UpdateItem],
    vault_service: VaultService,
    parser: MarkdownParser,
    note_sync: NoteSync,
    cache: ContentCache,
    logger: logging.Logger,
) -> set[Path]:
    """Point `_obsidian_source` at each card's line in the files the write-back rewrote.

    The card was synced with the line it had before its file was rewritten, and the
    rewrite moves the cards in it. Every card that reached Anki and moved gets one field
    write, all in one session, and its cached render the same line, so the next sync
    finds nothing to change. Anki holds the value this run compared or wrote, so
    nothing is read. Returns the files with a card that could not be corrected.
    """
    by_file: dict[Path, list[UpdateItem]] = {}
    for u in updates:
        if u.ok and u.note and u.source_file in written:
            by_file.setdefault(u.source_file, []).append(u)
    moved: list[tuple[UpdateItem, int, str, int]] = []
    for path, ups in by_file.items():
        lines = vault_service.card_lines(path)
        for u in ups:
            assert u.note is not None
            old = u.note.fields.get("_obsidian_source")
            line = lines.get(u.source_index)
            nid = u.new_nid or u.note.nid
            if old is None or line is None or not (nid and nid.isdigit()):
                continue
            new = parser.source_field(path, line, u.note.arete_id or "")
            if new is not None and new != old:
                moved.append((u, int(nid), new, line))
    if not moved:
        return set()
    errors = await note_sync.rewrite_fields(
        [(nid, u.note.model, {"_obsidian_source": new}) for u, nid, new, _ in moved if u.note]
    )
    failed: set[Path] = set()
    rows: list[tuple[Path, int, str, str]] = []
    for u, nid, new, line in moved:
        assert u.note is not None
        if errors.get(nid) is not None:
            logger.warning(f"[source] {u.source_file.name} #{u.source_index}: {errors[nid]}")
            failed.add(u.source_file)
            continue
        u.note.fields["_obsidian_source"] = new
        u.note.start_line = u.note.end_line = line
        if u.note.content_hash:
            rows.append(
                (u.source_file, u.source_index, u.note.content_hash, json.dumps(u.note.to_dict()))
            )
    if rows:
        try:
            cache.set_notes(rows)
        except Exception as e:  # the stale renders stay; the files are read afresh next run
            logger.warning(f"[source] could not cache the corrected lines: {e}")
            failed |= {path for path, *_ in rows}
    return failed


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
    anki_bridge: SyncBackend,
    cache: ContentCache,
    *,
    confirm_prune: bool = False,
    prompt: PruneConfirm | None = None,
) -> RunStats:
    """Sync the vault to Anki, then prune if asked.

    `confirm_prune` is the caller's explicit consent to delete. Without it prune asks
    `prompt` at a terminal, and anywhere else returns a preview and deletes nothing.
    Cards are synced by `NoteSync` over the bridge's note store, whichever backend it is.
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

    failed_files: set[Path] = set()  # a card here did not reach Anki; do not mark it synced

    # -------- Stage 1.5: ensure all cards have Arete IDs --------
    ids_total = 0
    # A file whose new ids could not be written: none of its cards is sent, because Anki
    # would receive ids the vault does not hold. Its declared nids stay in the inventory.
    unwritten_ids: set[Path] = set()
    updated_compatible: list[tuple[Path, dict[str, Any], bool]] = []
    for path, meta, is_fresh in compatible:
        if not any(isinstance(c, dict) and "id" not in c for c in meta.get("cards") or []):
            updated_compatible.append((path, meta, is_fresh))
            continue
        # Each new id is one inserted line; the downstream stages see the meta it gives.
        problems: list[tuple[str, str | None]] = []
        assigned, new_meta = 0, meta
        try:
            assigned, new_meta, refused = vault_service.assign_ids(path, dry_run=config.dry_run)
            problems = [
                (f"could not write new Arete ids: {why}; the file is unchanged", f"#{i + 1}")
                for i, why in refused.items()
            ]
        except Exception as e:
            problems = [(f"could not write new Arete ids: {e}", None)]
        if problems:
            logger.error(f"[id-error] {path}: {problems[0][0]}. Not syncing this file.")
            for msg, where in problems:
                recorder.add_error(path, msg, where)
            failed_files.add(path)
            unwritten_ids.add(path)
            recorder.add_inventory(
                [
                    {"nid": nid, "deck": None}
                    for c in meta.get("cards") or []
                    if isinstance(c, dict) and (nid := parser._extract_raw_nid(c))
                ]
            )
            updated_compatible.append((path, meta, is_fresh))
            continue
        if assigned:
            ids_total += assigned
            verb = "would assign" if config.dry_run else "Assigned"
            logger.info(f"[id] {verb} {assigned} new IDs in {path.name}")
        updated_compatible.append((path, new_meta, True))
    compatible = updated_compatible
    if ids_total > 0:
        logger.info(f"[id] Assigned {ids_total} new Arete IDs across vault")

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

    # -------- Stage 1.7: one Arete id declared by several cards --------
    # The id is the card's identity in Anki, so no copy may be sent: each would claim the
    # note of the others. Ids differing only in case are one tag in Anki.
    declared: dict[str, list[tuple[Path, int, str]]] = {}
    for path, meta, _fresh in compatible:
        for idx, card in enumerate(meta.get("cards") or [], start=1):
            if card_id := parser.declared_id(card):
                declared.setdefault(card_id.casefold(), []).append((path, idx, card_id))
    duplicated = {key: c for key, c in declared.items() if len(c) > 1}
    parser.duplicate_ids = {
        key: [f"{p.name} card#{i}" for p, i, _ in c] for key, c in duplicated.items()
    }
    for c in duplicated.values():
        for p, i, card_id in c:
            failed_files.add(p)
            recorder.add_error(
                p, f"Arete id {card_id} is declared by {len(c)} cards; none is synced", f"#{i}"
            )
    # A dry run decides through the same NoteSync, reading Anki and writing nothing.
    scanned = vault_claims(compatible, parser)
    note_sync = NoteSync(anki_bridge, scanned, logger, dry_run=config.dry_run)
    # Every note the vault names, read once: every card is compared with Anki, and the
    # batches compare against this read instead of reading their own.
    await note_sync.prefetch(scanned.nid_owners, vault_models(compatible))

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
            recorder.cards_cached_content += sum(1 for n in notes if n.unchanged)

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
                # A dry run's NoteSync reads and reports; the cache stays untouched below,
                # so the next real run still sees these cards as pending.
                async with sync_semaphore:
                    batch_updates = await note_sync.sync_notes(batch)

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
                    # One transaction per batch, written after Anki accepted these cards. A
                    # crash before the commit loses the rows; the cards are checked again.
                    if synced_rows:
                        cache.set_notes(synced_rows)
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
        if p not in unwritten_ids
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
        written = vault_service.apply_updates(updates, dry_run=config.dry_run)
        for path, index, why in vault_service.write_refused:
            failed_files.add(path)
            recorder.add_error(
                path, f"could not write its Anki ids: {why}; the file is unchanged", f"#{index}"
            )
        if not config.dry_run:
            failed_files |= await true_source_lines(
                written, updates, vault_service, parser, note_sync, cache, logger
            )

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
    total_imported = sum(1 for u in updates if u.ok and u.changed is not False)
    total_errors = len(recorder.errors)

    return RunStats(
        total_generated=total_generated,
        total_imported=total_imported,
        total_errors=total_errors,
        prune=prune_report,
    )
