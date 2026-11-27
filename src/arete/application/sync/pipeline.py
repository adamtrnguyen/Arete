import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tqdm import tqdm

from arete.application.config import AppConfig
from arete.application.sync.converter import markdown_to_anki_html
from arete.application.sync.note_sync import NoteSync, VaultClaims
from arete.application.sync.parser import MarkdownParser
from arete.application.sync.prune import PruneConfirm, PruneReport, prune_orphans
from arete.application.sync.vault_service import VaultService
from arete.application.utils.common import sanitize
from arete.application.utils.logging import RunRecorder
from arete.application.utils.media import FilenameIndex, transform_wikilinks_to_uri
from arete.application.utils.text import card_content_hash
from arete.domain.constants import CONSUMER_BATCH_SIZE
from arete.domain.identity import first_field_key
from arete.domain.interfaces import ContentCache, SyncBackend
from arete.domain.models import UpdateItem, WorkItem
from arete.domain.ownership import group_duplicate_ids


def _first_field_value(card: dict[str, Any], model: str) -> str:
    """Best-effort raw first field of a vault card dict for upfront front reservation."""
    mlow = model.lower()
    if mlow == "basic":
        return sanitize(card.get("Front") or "")
    if mlow == "cloze":
        return sanitize(card.get("Text") or "")
    _exclude = {"model", "deck", "tags", "id", "deps", "anki", "__line__"}
    for k, v in card.items():
        if k not in _exclude:
            return sanitize(v)
    return ""


def vault_claims(
    compatible: list[tuple[Path, dict[str, Any], bool]],
    parser: MarkdownParser,
    default_deck: str = "Default",
) -> VaultClaims:
    """Every nid, Arete id, and nid-bound front the scanned files declare, for `NoteSync`."""
    owners: dict[int, set[str | None]] = {}
    ids: set[str] = set()
    nid_fronts: dict[tuple[str, str, str], list[tuple[str, int, Path, int]]] = {}
    for path, meta, _fresh in compatible:
        file_model = sanitize(meta.get("model") or "Basic") or "Basic"
        file_deck = sanitize(meta.get("deck", "")) or default_deck
        for idx, card in enumerate(meta.get("cards") or [], start=1):
            card_id = parser.declared_id(card)
            key = card_id.casefold() if card_id else None
            if key:
                ids.add(key)
            raw = parser.extract_raw_nid(card) if isinstance(card, dict) else None
            if raw and raw.isdigit():
                nid = int(raw)
                owners.setdefault(nid, set()).add(key)
                if isinstance(card, dict):
                    model = sanitize(card.get("model") or file_model) or file_model
                    deck = sanitize(card.get("deck") or file_deck) or default_deck
                    raw_front = _first_field_value(card, model)
                    if raw_front:
                        fkey = first_field_key(
                            markdown_to_anki_html(
                                transform_wikilinks_to_uri(raw_front, parser.vault_root.name)
                            )
                        )
                        if fkey:
                            nid_fronts.setdefault(
                                (model.casefold(), deck.casefold(), fkey), []
                            ).append((f"{path.name} #{idx}", nid, path, idx))
    return VaultClaims(
        {nid: frozenset(o) for nid, o in owners.items()},
        frozenset(ids),
        {k: tuple(v) for k, v in nid_fronts.items()},
    )


def vault_models(compatible: list[tuple[Path, dict[str, Any], bool]]) -> set[str]:
    """The note types the scanned cards name: a card's own, else its file's, else Basic."""
    models: set[str] = set()
    for _path, meta, _fresh in compatible:
        default = sanitize(meta.get("model") or "Basic") or "Basic"
        for card in meta.get("cards") or []:
            if isinstance(card, dict):
                models.add(sanitize(card.get("model") or default) or default)
    return models


def _collect_moved_source_lines(
    written: set[Path],
    updates: list[UpdateItem],
    vault_service: VaultService,
    parser: MarkdownParser,
) -> list[tuple[UpdateItem, int, str, int]]:
    """Cards in rewritten files whose `_obsidian_source` line shifted after write-back."""
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
    return moved


async def true_source_lines(
    written: set[Path],
    updates: list[UpdateItem],
    vault_service: VaultService,
    parser: MarkdownParser,
    note_sync: NoteSync,
    cache: ContentCache,
    logger: logging.Logger,
) -> set[Path]:
    """Point `_obsidian_source` at each card's line in the files the write-back rewrote."""
    moved = _collect_moved_source_lines(written, updates, vault_service, parser)
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
        u.note.start_line = line
        if u.note.content_hash:
            u.note.content_hash = card_content_hash(
                u.note.model,
                u.note.deck,
                u.note.tags,
                u.note.fields,
                u.note.nid,
                u.note.cid,
            )
            rows.append(
                (u.source_file, u.source_index, u.note.content_hash, json.dumps(u.note.to_dict()))
            )
    if rows:
        try:
            cache.set_notes(rows)
        except Exception as e:
            logger.warning(f"[source] could not cache the corrected lines: {e}")
            failed |= {path for path, *_ in rows}
    return failed


@dataclass
class RunStats:
    total_generated: int
    total_imported: int
    total_errors: int
    prune: PruneReport | None = field(default=None)


def _assign_missing_ids(
    compatible: list[tuple[Path, dict[str, Any], bool]],
    vault_service: VaultService,
    parser: MarkdownParser,
    config: AppConfig,
    logger: logging.Logger,
    recorder: RunRecorder,
    failed_files: set[Path],
) -> tuple[list[tuple[Path, dict[str, Any], bool]], set[Path]]:
    """Stage 1.5: assign Arete IDs to cards lacking one; refuse files that fail writeback."""
    ids_total = 0
    unwritten_ids: set[Path] = set()
    updated_compatible: list[tuple[Path, dict[str, Any], bool]] = []
    for path, meta, is_fresh in compatible:
        if not any(
            isinstance(c, dict) and not parser.declared_id(c) for c in meta.get("cards") or []
        ):
            updated_compatible.append((path, meta, is_fresh))
            continue
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
                    if isinstance(c, dict) and (nid := parser.extract_raw_nid(c))
                ]
            )
            updated_compatible.append((path, meta, is_fresh))
            continue
        if assigned:
            ids_total += assigned
            verb = "would assign" if config.dry_run else "Assigned"
            logger.info(f"[id] {verb} {assigned} new IDs in {path.name}")
        updated_compatible.append((path, new_meta, True))
    if ids_total > 0:
        logger.info(f"[id] Assigned {ids_total} new Arete IDs across vault")
    return updated_compatible, unwritten_ids


def _detect_vault_conflicts(
    compatible: list[tuple[Path, dict[str, Any], bool]],
    parser: MarkdownParser,
    recorder: RunRecorder,
    failed_files: set[Path],
) -> None:
    """Stages 1.6–1.7: flag cross-file contested nids and duplicate Arete IDs."""
    claims: dict[str, list[tuple[Path, int]]] = {}
    for path, meta, _fresh in compatible:
        for idx, card in enumerate(meta.get("cards") or [], start=1):
            if isinstance(card, dict) and (nid := parser.extract_raw_nid(card)):
                claims.setdefault(nid, []).append((path, idx))
    contested = {nid: c for nid, c in claims.items() if len({p for p, _ in c}) > 1}
    parser.contested_nids = {
        nid: [f"{p.name} card#{i}" for p, i in c] for nid, c in contested.items()
    }
    for nid, c in contested.items():
        for p, i in c:
            failed_files.add(p)
            recorder.add_error(p, f"anki.nid {nid} is claimed by cards in several files", f"#{i}")

    duplicated = group_duplicate_ids(
        ((path, idx), card_id)
        for path, meta, _fresh in compatible
        for idx, card in enumerate(meta.get("cards") or [], start=1)
        if (card_id := parser.declared_id(card))
    )
    parser.duplicate_ids = {
        key: [f"{p.name} card#{i}" for (p, i), _ in c] for key, c in duplicated.items()
    }
    for c in duplicated.values():
        for (p, i), card_id in c:
            failed_files.add(p)
            recorder.add_error(
                p,
                f"Arete id {card_id} is declared by {len(c)} cards; none is synced",
                f"#{i}",
            )


def _record_batch_updates(
    batch_updates: list[UpdateItem],
    updates: list[UpdateItem],
    recorder: RunRecorder,
    failed_files: set[Path],
    cache: ContentCache,
    *,
    dry_run: bool,
) -> None:
    """Record one consumer batch's results into `updates`, `recorder`, and the hot cache."""
    synced_rows: list[tuple[Path, int, str, str]] = []
    for u in batch_updates:
        updates.append(u)
        if not u.ok:
            recorder.cards_failed += 1
            failed_files.add(u.source_file)
            recorder.add_error(u.source_file, f"Sync fail: {u.error}", f"#{u.source_index}")
            continue
        recorder.cards_synced += 1
        if u.new_nid:
            recorder.add_inventory([{"nid": u.new_nid, "deck": u.note.deck if u.note else None}])
        if u.note and u.note.content_hash and not dry_run:
            prev_nid, prev_cid = u.note.nid, u.note.cid
            u.note.nid = u.new_nid or u.note.nid
            u.note.cid = u.new_cid or u.note.cid
            if (u.note.nid, u.note.cid) != (prev_nid, prev_cid):
                u.note.content_hash = card_content_hash(
                    u.note.model,
                    u.note.deck,
                    u.note.tags,
                    u.note.fields,
                    u.note.nid,
                    u.note.cid,
                )
            synced_rows.append(
                (u.source_file, u.source_index, u.note.content_hash, json.dumps(u.note.to_dict()))
            )
    if synced_rows:
        cache.set_notes(synced_rows)


async def _drain_batch(work_q: asyncio.Queue[WorkItem | None]) -> list[WorkItem] | None:
    """Pop one batch of work items from `work_q`, or None on sentinel."""
    first_item = await work_q.get()
    if first_item is None:
        work_q.task_done()
        return None
    batch = [first_item]
    while len(batch) < CONSUMER_BATCH_SIZE:
        try:
            next_item = work_q.get_nowait()
            if next_item is None:
                await work_q.put(None)
                break
            batch.append(next_item)
        except asyncio.QueueEmpty:
            break
    return batch


async def _consume_batches(
    work_q: asyncio.Queue[WorkItem | None],
    sync_semaphore: asyncio.Semaphore,
    updates_lock: asyncio.Lock,
    note_sync: Any,
    updates: list[UpdateItem],
    recorder: RunRecorder,
    failed_files: set[Path],
    cache: ContentCache,
    logger: logging.Logger,
    *,
    dry_run: bool,
) -> None:
    """Drain batches from `work_q`, sync through `note_sync`, and record outcomes."""
    while (batch := await _drain_batch(work_q)) is not None:
        try:
            async with sync_semaphore:
                batch_updates = await note_sync.sync_notes(batch)
            async with updates_lock:
                _record_batch_updates(
                    batch_updates, updates, recorder, failed_files, cache, dry_run=dry_run
                )
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


async def _run_sync_workers(
    compatible: list[tuple[Path, dict[str, Any], bool]],
    unwritten_ids: set[Path],
    parser: MarkdownParser,
    cache: ContentCache,
    name_index: FilenameIndex,
    note_sync: Any,
    config: AppConfig,
    logger: logging.Logger,
    recorder: RunRecorder,
    failed_files: set[Path],
    unparsed_files: list[Path],
) -> list[UpdateItem]:
    """Stage 3: run file producers and batched NoteSync consumers."""
    work_q: asyncio.Queue[WorkItem | None] = asyncio.Queue(maxsize=max(1, config.queue_size))
    updates: list[UpdateItem] = []
    updates_lock = asyncio.Lock()
    max_sync_concurrency = max(1, config.workers)
    sync_semaphore = asyncio.Semaphore(max_sync_concurrency)
    prod_semaphore = asyncio.Semaphore(max_sync_concurrency)

    async def producer_file(md_file: Path, meta: dict[str, Any], is_fresh: bool) -> None:
        async with prod_semaphore:
            try:
                notes, skipped_indices, inventory = parser.parse_file(
                    md_file, meta, cache, name_index, is_fresh
                )
                recorder.cards_generated += len(notes) + len(skipped_indices)
                recorder.add_inventory(inventory)
                for note in notes:
                    await work_q.put(
                        WorkItem(note=note, source_file=md_file, source_index=note.source_index)
                    )
            except Exception as e:
                logger.error(f"[producer-error] {md_file}: {e}")
                recorder.add_error(md_file, str(e))
                unparsed_files.append(md_file)
                failed_files.add(md_file)

    consumers = [
        asyncio.create_task(
            _consume_batches(
                work_q,
                sync_semaphore,
                updates_lock,
                note_sync,
                updates,
                recorder,
                failed_files,
                cache,
                logger,
                dry_run=config.dry_run,
            )
        )
        for _ in range(max_sync_concurrency)
    ]
    producer_tasks = [
        asyncio.create_task(producer_file(p, meta, is_fresh))
        for p, meta, is_fresh in compatible
        if p not in unwritten_ids
    ]

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

    for _ in range(max_sync_concurrency):
        await work_q.put(None)
    await asyncio.gather(*consumers)
    return updates


async def _persist_writeback(
    updates: list[UpdateItem],
    compatible: list[tuple[Path, dict[str, Any], bool]],
    vault_service: VaultService,
    parser: MarkdownParser,
    note_sync: Any,
    cache: ContentCache,
    config: AppConfig,
    logger: logging.Logger,
    recorder: RunRecorder,
    failed_files: set[Path],
) -> None:
    """Stage 4: persist assigned NIDs/CIDs, refresh source lines, and record clean files."""
    if updates:
        logger.info("[pipeline] Persisting NIDs/CIDs to frontmatter...")
        written = vault_service.apply_updates(updates, dry_run=config.dry_run)
        for path, index, why in vault_service.write_refused:
            failed_files.add(path)
            recorder.add_error(
                path,
                f"could not write its Anki ids: {why}; the file is unchanged",
                f"#{index}",
            )
        if not config.dry_run:
            failed_files |= await true_source_lines(
                written, updates, vault_service, parser, note_sync, cache, logger
            )

    if not config.dry_run:
        vault_service.record_synced(
            p for p, _meta, is_fresh in compatible if is_fresh and p not in failed_files
        )


async def _run_prune_stage(
    config: AppConfig,
    recorder: RunRecorder,
    anki_bridge: SyncBackend,
    vault_service: VaultService,
    unparsed_files: list[Path],
    scanned: VaultClaims,
    logger: logging.Logger,
    *,
    confirm_prune: bool,
    prompt: PruneConfirm | None,
) -> PruneReport | None:
    """Stage 5: prune orphaned Arete notes/decks unless unreadable files block safe pruning."""
    if not config.prune:
        return None
    blockers = [f"{p.name} ({why})" for p, why in vault_service.unreadable]
    blockers += [f"{p.name} (parse crashed)" for p in unparsed_files]
    if blockers:
        msg = (
            "these Arete files could not be read, so their notes cannot be told apart "
            f"from orphans. Fix them and prune again: {', '.join(blockers)}"
        )
        logger.warning(f"[prune] REFUSED: {msg}")
        return PruneReport("refused", msg)
    return await prune_orphans(
        config,
        recorder,
        anki_bridge,
        logger,
        declared_ids=scanned.arete_ids,
        confirmed=confirm_prune,
        prompt=prompt,
    )


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
    note_sync_factory: Callable[..., Any] = NoteSync,
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

    for bad_path, why in vault_service.unreadable:
        logger.error(f"[vault-error] {bad_path}: unreadable Arete file ({why})")
        recorder.add_error(bad_path, f"unreadable Arete file ({why})")

    updates: list[UpdateItem] = []
    unparsed_files: list[Path] = []
    scanned = VaultClaims()

    if not compatible:
        logger.info("No compatible markdown files found.")
    else:
        failed_files: set[Path] = set()
        compatible, unwritten_ids = _assign_missing_ids(
            compatible, vault_service, parser, config, logger, recorder, failed_files
        )
        _detect_vault_conflicts(compatible, parser, recorder, failed_files)

        scanned = vault_claims(compatible, parser, default_deck=config.default_deck)
        note_sync = note_sync_factory(anki_bridge, scanned, logger, dry_run=config.dry_run)
        await note_sync.prefetch(scanned.nid_owners, vault_models(compatible))

        assert config.vault_root is not None
        name_index = FilenameIndex(config.vault_root, logger)
        updates = await _run_sync_workers(
            compatible,
            unwritten_ids,
            parser,
            cache,
            name_index,
            note_sync,
            config,
            logger,
            recorder,
            failed_files,
            unparsed_files,
        )
        await _persist_writeback(
            updates,
            compatible,
            vault_service,
            parser,
            note_sync,
            cache,
            config,
            logger,
            recorder,
            failed_files,
        )

    prune_report = await _run_prune_stage(
        config,
        recorder,
        anki_bridge,
        vault_service,
        unparsed_files,
        scanned,
        logger,
        confirm_prune=confirm_prune,
        prompt=prompt,
    )
    return RunStats(
        total_generated=len(updates),
        total_imported=sum(1 for u in updates if u.ok and u.changed is not False),
        total_errors=len(recorder.errors),
        prune=prune_report,
    )
