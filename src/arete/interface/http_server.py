import logging
import os
import signal
import threading
import time
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

VERSION = version("arete")

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("arete.server")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Startup
    logger.info(f"Arete Server v{VERSION} starting up...")
    yield
    # Shutdown
    logger.info("Arete Server shutting down...")


app = FastAPI(
    title="Arete Server",
    description="Background server for Arete Obsidian plugin.",
    version=VERSION,
    lifespan=lifespan,
)


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: float


start_time = time.time()


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Check that the server is reachable."""
    return HealthResponse(status="ok", version=VERSION, uptime_seconds=time.time() - start_time)


@app.get("/version")
async def get_version():
    return {"version": VERSION}


# Request model for sync parameters (subset of AppConfig settings)
class SyncRequest(BaseModel):
    # If None, use defaults/config file.
    vault_root: str | None = None
    file_path: str | None = None  # sync single file
    backend: str | None = None  # auto, direct, ankiconnect
    force: bool | None = None
    prune: bool | None = None
    clear_cache: bool | None = None
    dry_run: bool | None = None
    anki_connect_url: str | None = None
    workers: int | None = None


class SyncStatsResponse(BaseModel):
    total_generated: int
    total_imported: int
    total_errors: int
    success: bool
    # We could include error list, but might be too large.
    # Just return count/status for now.


@app.post("/sync", response_model=SyncStatsResponse)
async def trigger_sync(req: SyncRequest):
    """Trigger a sync operation."""
    from arete.application.config import resolve_config
    from arete.application.orchestrator import execute_sync

    logger.info(f"Sync requested via API: {req}")

    # Map request to overrides dict
    overrides = {
        "vault_root": req.vault_root,
        "root_input": req.file_path,  # single file sync basically sets root input
        "backend": req.backend,
        "force": req.force,
        "prune": req.prune,
        "clear_cache": req.clear_cache,
        "dry_run": req.dry_run,
        "anki_connect_url": req.anki_connect_url,
        "workers": req.workers,
    }
    # Filter Nones
    overrides = {k: v for k, v in overrides.items() if v is not None}

    try:
        # Resolve config
        config = resolve_config(overrides)

        # Execute
        stats = await execute_sync(config)

        return SyncStatsResponse(
            total_generated=stats.total_generated,
            total_imported=stats.total_imported,
            total_errors=stats.total_errors,
            success=stats.total_errors == 0,
        )
    except Exception as e:
        logger.error(f"Sync failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


class FormatRequest(BaseModel):
    vault_root: str | None = None
    dry_run: bool | None = None


class FormatResponse(BaseModel):
    formatted_count: int
    success: bool


@app.post("/vault/format", response_model=FormatResponse)
async def format_vault(req: FormatRequest):
    """Format and normalize YAML in the entire vault."""
    from arete.application.config import resolve_config
    from arete.application.factory import get_vault_service

    logger.info(f"Format requested via API: {req}")

    overrides = {
        "vault_root": req.vault_root,
        "dry_run": req.dry_run,
    }
    overrides = {k: v for k, v in overrides.items() if v is not None}

    try:
        config = resolve_config(overrides)
        vault = get_vault_service(config)
        count = vault.format_vault(dry_run=config.dry_run)

        return FormatResponse(formatted_count=count, success=True)
    except Exception as e:
        logger.error(f"Format failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


class CardsRequest(BaseModel):
    cids: list[int]
    backend: str | None = None
    anki_connect_url: str | None = None
    anki_base: str | None = None


@app.post("/anki/cards/suspend")
async def suspend_cards(req: CardsRequest):
    """Suspend cards by Card IDs."""
    from arete.application.config import resolve_config
    from arete.application.factory import get_anki_bridge

    try:
        overrides = {
            "backend": req.backend,
            "anki_connect_url": req.anki_connect_url,
            "anki_base": req.anki_base,
        }
        config = resolve_config({k: v for k, v in overrides.items() if v is not None})
        anki = await get_anki_bridge(config)
        try:
            return {"ok": await anki.suspend_cards(req.cids)}
        finally:
            await anki.close()
    except Exception as e:
        logger.error(f"Suspend failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/anki/cards/unsuspend")
async def unsuspend_cards(req: CardsRequest):
    """Unsuspend cards by Card IDs."""
    from arete.application.config import resolve_config
    from arete.application.factory import get_anki_bridge

    try:
        overrides = {
            "backend": req.backend,
            "anki_connect_url": req.anki_connect_url,
            "anki_base": req.anki_base,
        }
        config = resolve_config({k: v for k, v in overrides.items() if v is not None})
        anki = await get_anki_bridge(config)
        try:
            return {"ok": await anki.unsuspend_cards(req.cids)}
        finally:
            await anki.close()
    except Exception as e:
        logger.error(f"Unsuspend failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/anki/models/{name}/styling")
async def get_model_styling(
    name: str,
    backend: str | None = None,
    anki_connect_url: str | None = None,
    anki_base: str | None = None,
):
    from arete.application.config import resolve_config
    from arete.application.factory import get_anki_bridge

    try:
        overrides = {
            "backend": backend,
            "anki_connect_url": anki_connect_url,
            "anki_base": anki_base,
        }
        config = resolve_config({k: v for k, v in overrides.items() if v is not None})
        anki = await get_anki_bridge(config)
        try:
            css = await anki.get_model_styling(name)
            return {"css": css}
        finally:
            await anki.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/anki/models/{name}/templates")
async def get_model_templates(
    name: str,
    backend: str | None = None,
    anki_connect_url: str | None = None,
    anki_base: str | None = None,
):
    from arete.application.config import resolve_config
    from arete.application.factory import get_anki_bridge

    try:
        overrides = {
            "backend": backend,
            "anki_connect_url": anki_connect_url,
            "anki_base": anki_base,
        }
        config = resolve_config({k: v for k, v in overrides.items() if v is not None})
        anki = await get_anki_bridge(config)
        try:
            templates = await anki.get_model_templates(name)
            return templates
        finally:
            await anki.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/shutdown")
async def shutdown_server():
    """Gracefully shut down the server.

    Useful for plugins to kill the process when they unload.
    """
    logger.info("Received shutdown request.")

    # Schedule the kill provided we are running in Uvicorn
    # There isn't a standard "fastapi shutdown" method, but we can kill the process
    # or rely on uvicorn's handling if we can access the server instance.
    # A simple reliable way for a CLI tool is to exit the process.

    def kill():
        time.sleep(0.5)  # Give time to return response
        logger.info("Exiting process...")
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=kill).start()
    return {"message": "Server shutting down..."}


class StatsRequest(BaseModel):
    nids: list[int]
    backend: str | None = None
    anki_connect_url: str | None = None
    anki_base: str | None = None


@app.post("/anki/stats")
async def get_stats(req: StatsRequest):
    """Get stats for a list of Note IDs.

    Uses the configured backend (Auto/Direct/Connect).
    """
    from arete.application.config import resolve_config

    try:
        # Pass overrides from request to config
        overrides = {
            "backend": req.backend,
            "anki_connect_url": req.anki_connect_url,
            "anki_base": req.anki_base,
        }
        # Filter Nones
        overrides = {k: v for k, v in overrides.items() if v is not None}

        config = resolve_config(overrides)

        from arete.application.factory import get_stats_service

        service = get_stats_service(config)
        stats = await service.get_enriched_stats(req.nids)
        return stats
    except Exception as e:
        logger.error(f"Stats fetch failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


class BrowseRequest(BaseModel):
    query: str
    backend: str | None = None
    anki_connect_url: str | None = None
    anki_base: str | None = None


@app.post("/anki/browse")
async def browse_anki(req: BrowseRequest):
    """Open the Anki browser with a query."""
    from arete.application.config import resolve_config
    from arete.application.factory import get_anki_bridge

    try:
        overrides = {
            "backend": req.backend,
            "anki_connect_url": req.anki_connect_url,
            "anki_base": req.anki_base,
        }
        config = resolve_config({k: v for k, v in overrides.items() if v is not None})
        anki = await get_anki_bridge(config)
        try:
            return {"ok": await anki.gui_browse(req.query)}
        finally:
            await anki.close()
    except Exception as e:
        logger.error(f"Browse failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- Queue Builder Endpoints ---


class DecksRequest(BaseModel):
    backend: str | None = None
    anki_connect_url: str | None = None
    anki_base: str | None = None


@app.post("/anki/decks")
async def get_decks(req: DecksRequest):
    """Get all deck names from Anki."""
    from arete.application.config import resolve_config
    from arete.application.factory import get_anki_bridge

    try:
        overrides = {
            "backend": req.backend,
            "anki_connect_url": req.anki_connect_url,
            "anki_base": req.anki_base,
        }
        config = resolve_config({k: v for k, v in overrides.items() if v is not None})
        anki = await get_anki_bridge(config)
        try:
            decks = await anki.get_deck_names()
            return {"decks": decks}
        finally:
            await anki.close()
    except Exception as e:
        logger.error(f"Get decks failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


class QueueBuildRequest(BaseModel):
    deck: str | None = None
    depth: int = 2
    max_cards: int = 50
    vault_root: str | None = None
    backend: str | None = None
    anki_connect_url: str | None = None
    anki_base: str | None = None


@app.post("/queue/build")
async def build_queue(req: QueueBuildRequest):
    """Build a study queue from due cards with prerequisites."""
    from pathlib import Path

    from arete.application.config import resolve_config
    from arete.application.factory import get_anki_bridge
    from arete.application.queue.service import build_study_queue

    try:
        overrides = {
            "vault_root": req.vault_root,
            "backend": req.backend,
            "anki_connect_url": req.anki_connect_url,
            "anki_base": req.anki_base,
        }
        config = resolve_config({k: v for k, v in overrides.items() if v is not None})

        if not config.vault_root:
            raise HTTPException(status_code=400, detail="Vault root not configured.")

        anki = await get_anki_bridge(config)
        try:
            vault_root = Path(config.vault_root)

            result = await build_study_queue(
                anki,
                vault_root,
                deck=req.deck,
                depth=req.depth,
                max_cards=req.max_cards,
                algo="simple",
                dry_run=True,  # /queue/build only plans; /queue/create-deck creates
                enrich=True,
            )

            return {
                "deck": req.deck or "All Decks",
                "due_count": result.due_count - result.unmapped_count,
                "total_with_prereqs": result.total_queued,
                "queue": [
                    {
                        "position": item.position,
                        "id": item.id,
                        "title": item.title,
                        "file": item.file,
                        "is_prereq": item.is_prereq,
                    }
                    for item in result.queue_items
                ],
            }
        finally:
            await anki.close()
    except HTTPException:
        # Re-raise HTTPExceptions (like the 400 validation error)
        raise
    except Exception as e:
        logger.error(f"Queue build failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


class QueueCreateDeckRequest(BaseModel):
    card_ids: list[str]
    deck_name: str = "Arete Study Queue"
    backend: str | None = None
    anki_connect_url: str | None = None
    anki_base: str | None = None
    reschedule: bool = True  # Default to True (Study Mode)


@app.post("/queue/create-deck")
async def create_queue_deck(req: QueueCreateDeckRequest):
    """Create a filtered deck in Anki with queue ordering tags."""
    from arete.application.config import resolve_config
    from arete.application.factory import get_anki_bridge

    logger.info(f"Create queue deck requested: {len(req.card_ids)} cards")

    try:
        overrides = {
            "backend": req.backend,
            "anki_connect_url": req.anki_connect_url,
            "anki_base": req.anki_base,
        }
        config = resolve_config({k: v for k, v in overrides.items() if v is not None})
        anki = await get_anki_bridge(config)
        try:
            # 1. Resolve to CIDs
            cids = await anki.get_card_ids_for_arete_ids(req.card_ids)
            if not cids:
                # Maybe the user hasn't synced?
                return {"ok": False, "message": "No matching Anki cards found. Have you synced?"}

            # 2. Create Deck
            success = await anki.create_topo_deck(req.deck_name, cids, reschedule=req.reschedule)

            if success:
                return {
                    "ok": True,
                    "message": f"Created deck '{req.deck_name}' with {len(cids)} cards.",
                }
            else:
                return {"ok": False, "message": "Failed to create deck (check logs)."}
        finally:
            await anki.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create deck failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
