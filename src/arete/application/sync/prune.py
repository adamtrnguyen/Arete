"""Prune: delete the Anki notes and decks Arete made that the vault no longer claims.

Three rules keep this from touching anything else:

1. Ownership. Only a note carrying an Arete id tag (`domain/ownership.py`) can be an
   orphan. A note without one was made by hand or by another tool and is never deleted,
   whatever deck it is in. Notes with a card under Default are never deleted either.
2. Decks. A deck is deleted only if it is not Default, not a vault deck or a parent of
   one, holds at least one note, every note in it (subdecks included) is an Arete
   orphan, and every subdeck is deletable by the same rule. An empty deck is left alone:
   Anki records no creator for a deck, so there is no way to tell that Arete made it.
   It is listed in the report as "empty, not deleted".
3. Confirmation. Nothing is deleted unless the caller confirmed (`--yes`,
   `confirm_prune=true`) or a person typed "yes" at a terminal prompt. Off a terminal
   (MCP, HTTP, an agent) the prompt is never shown: the run returns the preview.

Background: docs/history/prune-and-path-containment.md.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Literal

from arete.application.config import AppConfig
from arete.application.utils.logging import RunRecorder
from arete.domain.interfaces import SyncPort
from arete.domain.models import AnkiDeck
from arete.domain.ownership import is_arete_id

PruneStatus = Literal[
    "clean",  # nothing to delete
    "deleted",  # confirmed and done
    "failed",  # confirmed, but Anki refused a delete
    "unconfirmed",  # preview only: no confirmation and no terminal to ask on
    "aborted",  # a person answered something other than "yes"
    "dry_run",  # preview only: --dry-run
    "skipped",  # the run did not cover the whole vault
    "refused",  # a vault file could not be read, so orphans cannot be told apart
]

# Shown a preview, return True to delete. Called only when stdin is a terminal.
PruneConfirm = Callable[["PruneReport"], bool]


@dataclass
class OrphanNote:
    nid: int
    arete_id: str
    deck: str


@dataclass
class PruneReport:
    status: PruneStatus
    message: str = ""
    decks: list[str] = field(default_factory=list)  # deleted, or that would be
    notes: list[OrphanNote] = field(default_factory=list)  # deleted, or that would be
    empty_decks_not_deleted: list[str] = field(default_factory=list)
    decks_kept: list[str] = field(default_factory=list)  # hold a note prune may not delete

    @property
    def deletes_anything(self) -> bool:
        return bool(self.decks or self.notes)

    def to_dict(self) -> dict:
        out = asdict(self)
        out["deck_count"] = len(self.decks)
        out["note_count"] = len(self.notes)
        return out


def _is_default(deck: str) -> bool:
    return deck == "Default" or deck.startswith("Default::")


def _arete_orphans(
    all_nids: list[int], tags: dict[int, list[str]], valid_nids: set[str], default_nids: set[int]
) -> dict[int, str]:
    """{nid: arete_id} for each note Arete owns that the vault no longer claims."""
    orphans: dict[int, str] = {}
    for nid in all_nids:
        owner = next((t for t in tags.get(nid, []) if is_arete_id(t)), None)
        if owner and str(nid) not in valid_nids and nid not in default_nids:
            orphans[nid] = owner
    return orphans


def _decide_decks(
    report: PruneReport,
    notes_by_deck: dict[str, set[int]],
    orphans: set[int],
    protected: set[str],
    valid_nids: set[str],
    logger: logging.Logger,
) -> None:
    """Fill report.decks, .empty_decks_not_deleted and .decks_kept."""
    candidates = list(notes_by_deck)
    deletable: set[str] = set()
    # Deepest first, so a deck is decided after all of its subdecks.
    for d in sorted(candidates, key=lambda name: name.count("::"), reverse=True):
        if d in protected:
            continue
        notes = notes_by_deck[d]
        if not notes:
            report.empty_decks_not_deleted.append(d)
            continue
        claimed = [n for n in notes if str(n) in valid_nids]
        if claimed:
            logger.warning(
                f"[prune] keeping deck {d!r}: it holds {len(claimed)} note(s) the vault "
                "still claims. Run 'arete sync --force' to move them back."
            )
        subdecks = [c for c in candidates if c.startswith(d + "::")]
        if notes <= orphans and all(c in deletable for c in subdecks):
            deletable.add(d)
        else:
            report.decks_kept.append(d)

    # Delete the top of each deletable subtree; Anki removes the subdecks with it.
    report.decks = sorted(
        d for d in deletable if not any(p in deletable for p in AnkiDeck(name=d).parents)
    )
    report.empty_decks_not_deleted.sort()
    report.decks_kept.sort()


async def plan_prune(
    bridge: SyncPort, valid_nids: set[str], valid_decks: set[str], logger: logging.Logger
) -> PruneReport:
    """Work out what prune would delete. Reads Anki; changes nothing."""
    anki_decks = await bridge.get_deck_names()
    candidates = [d for d in anki_decks if not _is_default(d)]
    if not candidates:
        return PruneReport("clean", "No non-default decks found.")

    protected = set(valid_decks)
    for d in valid_decks:
        protected.update(AnkiDeck(name=d).parents)

    default_nids: set[int] = set()
    if any(_is_default(d) for d in anki_decks):
        default_nids = set((await bridge.get_notes_in_deck("Default")).values())

    # A deck search includes subdecks, so each deck's notes cover its whole subtree.
    notes_by_deck = {d: set((await bridge.get_notes_in_deck(d)).values()) for d in candidates}
    all_nids = sorted(set().union(*notes_by_deck.values()))
    tags = await bridge.get_note_tags(all_nids) if all_nids else {}
    orphans = _arete_orphans(all_nids, tags, valid_nids, default_nids)

    report = PruneReport("clean")
    _decide_decks(report, notes_by_deck, set(orphans), protected, valid_nids, logger)

    def deck_of(nid: int) -> str:
        return max((d for d, ns in notes_by_deck.items() if nid in ns), key=len)

    report.notes = [OrphanNote(nid, orphans[nid], deck_of(nid)) for nid in sorted(orphans)]
    return report


def _log_preview(report: PruneReport, valid_nids: int, valid_decks: int, log: logging.Logger):
    log.warning("\n" + "=" * 40)
    log.warning("PRUNE SUMMARY")
    log.info(f"Valid NIDs found in vault: {valid_nids}")
    log.info(f"Valid Decks found in vault: {valid_decks}")
    log.warning("-" * 20)
    log.warning(f"Arete decks to DELETE: {len(report.decks)} (each holds only Arete orphans)")
    for d in report.decks:
        log.warning(f"  - [DECK] {d}")
    log.warning(f"Arete orphan notes to DELETE: {len(report.notes)}")
    for n in report.notes[:20]:
        log.warning(f"  - [NOTE] {n.arete_id} (nid {n.nid}) in {n.deck}")
    if len(report.notes) > 20:
        log.warning(f"  ... and {len(report.notes) - 20} more")
    if report.empty_decks_not_deleted:
        log.info(
            f"Empty decks, not deleted ({len(report.empty_decks_not_deleted)}): "
            + ", ".join(report.empty_decks_not_deleted)
        )
    if report.decks_kept:
        log.info(f"Decks kept, they hold notes prune may not delete ({len(report.decks_kept)}).")


async def _delete(report: PruneReport, bridge: SyncPort, logger: logging.Logger) -> PruneReport:
    if report.notes:
        logger.info(f"[prune] Deleting {len(report.notes)} notes...")
        try:
            await bridge.delete_notes([n.nid for n in report.notes])
        except Exception as e:
            logger.error(f"[prune] Failed to delete notes: {e}")
            report.status = "failed"
            report.message = f"Failed to delete notes: {e}. No deck was deleted."
            return report

    if report.decks:
        # Delete only decks that are empty now. A note added since the plan was made
        # (or one Anki refused to delete) keeps its deck.
        empty = [d for d in report.decks if not await bridge.get_notes_in_deck(d)]
        for d in sorted(set(report.decks) - set(empty)):
            logger.warning(f"[prune] keeping deck {d!r}: it is not empty after the notes went.")
        report.decks = empty
        if empty:
            logger.info(f"[prune] Deleting {len(empty)} decks...")
            try:
                await bridge.delete_decks(empty)
            except Exception as e:
                logger.error(f"[prune] Failed to delete decks: {e}")
                report.status = "failed"
                report.message = f"Failed to delete decks: {e}"
                return report

    report.status = "deleted"
    report.message = f"Deleted {len(report.notes)} notes and {len(report.decks)} decks."
    logger.info("[prune] Pruning complete.")
    return report


async def prune_orphans(
    config: AppConfig,
    recorder: RunRecorder,
    bridge: SyncPort,
    logger: logging.Logger,
    *,
    confirmed: bool = False,
    prompt: PruneConfirm | None = None,
) -> PruneReport:
    """Plan the prune, then delete only if confirmed. See the module docstring."""
    if config.root_input != config.vault_root:
        msg = "Pruning requires running on the entire vault root to ensure safety."
        logger.warning(f"[prune] SKIPPED: {msg}")
        return PruneReport("skipped", msg)

    logger.info("[prune] analyzing vault vs anki state...")
    valid_nids = {str(n) for n in recorder.inventory_nids}
    valid_decks = set(recorder.inventory_decks)
    report = await plan_prune(bridge, valid_nids, valid_decks, logger)

    if not report.deletes_anything:
        report.status = "clean"
        report.message = report.message or "No orphans found."
        logger.info(f"[prune] Clean. {report.message}")
        return report

    _log_preview(report, len(valid_nids), len(valid_decks), logger)

    if config.dry_run:
        report.status = "dry_run"
        report.message = "Dry run: nothing was deleted."
        logger.warning("[prune] DRY RUN: Destructive actions skipped.")
        return report

    if not confirmed:
        if prompt is None or not sys.stdin.isatty():
            report.status = "unconfirmed"
            report.message = (
                "Prune not confirmed, so nothing was deleted. Review the notes and decks "
                "listed, then confirm with --yes (CLI) or confirm_prune=true (MCP, HTTP)."
            )
            logger.warning(f"[prune] {report.message}")
            return report
        if not prompt(report):
            report.status = "aborted"
            report.message = "Aborted at the prompt: nothing was deleted."
            logger.info("[prune] Aborted by user.")
            return report

    return await _delete(report, bridge, logger)
