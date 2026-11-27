"""Shared HTTP / WSL transport client for AnkiConnect.

Used by both `AnkiConnectAdapter` (sync, queue, admin) and `ConnectStatsRepository`
(FSRS / revlog stats) so both share WSL `curl.exe` bridging, connection-pool limits,
resend-safety retry semantics, and clean lifecycle closing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import shutil
from contextlib import suppress
from typing import Any

import httpx

from arete.domain.constants import REQUEST_TIMEOUT, RESPONSIVENESS_TIMEOUT, SYNC_CONCURRENCY
from arete.domain.models import OutcomeUnknown

DEFAULT_ANKI_CONNECT_URL = "http://127.0.0.1:8765"

# Whether an action may be sent again after a timeout or dropped connection (when Anki
# may already have applied it). Anything absent defaults to False; `multi` takes
# its least safe action.
_RESEND_SAFE: dict[str, bool] = {
    # reads
    "version": True,
    "deckNames": True,
    "getDeckConfig": True,
    "findNotes": True,
    "findCards": True,
    "notesInfo": True,
    "cardsInfo": True,
    "getFSRSStats": True,
    "getReviewsOfCards": True,
    "modelTemplates": True,
    "modelStyling": True,
    "getModelInfo": True,
    "getHomeDecks": True,
    # writes that set a state: a second run finds it set
    "createDeck": True,  # decks.id() returns the existing deck
    "setHomeDeck": True,  # sets the home deck of the listed cards
    "updateNoteFields": True,  # sets the listed fields
    "addTags": True,
    "removeTags": True,
    "modelFieldAdd": True,  # adds the field only if the type lacks it
    "suspend": True,  # sets the queue; a second run answers False
    "unsuspend": True,
    "deleteNotes": True,
    "deleteDecks": True,  # skips a deck that no longer exists
    "createFilteredDeck": True,  # empties and rebuilds the same deck
    "guiBrowse": True,
    # writes that are not: a second addNote makes a second note (or is refused as a
    # duplicate although the first landed); a second changeNoteType re-maps fields that
    # were mapped for the old type onto the converted note
    "addNote": False,
    "changeNoteType": False,
}

# Failures that prove the request never reached Anki: no connection was made.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_ATTEMPTS = 3
_RETRY_DELAY = 1.0  # seconds before sending again after a timeout
# A refused connection almost always means Anki is not running, and the caller falls back
# to the closed-Anki path only once this gives up; so it waits only briefly.
_NOT_SENT_DELAY = 0.1


def _is_resend_safe(action: str, params: dict) -> bool:
    if action == "multi":
        subs = params.get("actions") or []
        return all(_is_resend_safe(s.get("action", ""), s.get("params") or {}) for s in subs)
    return _RESEND_SAFE.get(action, False)


def _unknown_text(action: str, how: str) -> str:
    return f"AnkiConnect '{action}' {how}: Anki may or may not have applied it"


class AnkiConnectClient:
    """Async JSON-RPC transport for AnkiConnect with WSL bridging and resend-safety."""

    def __init__(self, url: str = DEFAULT_ANKI_CONNECT_URL):
        """Initialize transport for `url`, auto-detecting WSL bridge if needed."""
        self.logger = logging.getLogger(__name__)
        self.use_windows_curl = False
        self._client: httpx.AsyncClient | None = None
        self._invoke_sem = asyncio.Semaphore(SYNC_CONCURRENCY)

        if "microsoft" in platform.uname().release.lower():
            curl_path = shutil.which("curl.exe")
            if curl_path:
                self.use_windows_curl = True
                if "127.0.0.1" in url or "localhost" in url:
                    url = url.replace("localhost", "127.0.0.1")
                self.logger.info(
                    f"WSL detected: Using curl.exe bridge (found at {curl_path}) to talk to {url}"
                )
                self.url = url
                return
            self.logger.debug("WSL detected but 'curl.exe' not found in PATH.")

            if "localhost" in url or "127.0.0.1" in url:
                try:
                    with open("/etc/resolv.conf", encoding="utf-8") as f:
                        for line in f:
                            if line.startswith("nameserver"):
                                host_ip = line.split()[1].strip()
                                url = url.replace("localhost", host_ip).replace(
                                    "127.0.0.1", host_ip
                                )
                                self.logger.info(
                                    f"WSL detected: Auto-corrected URL using resolv.conf to "
                                    f"http://{host_ip}:8765"
                                )
                                break
                except Exception as e:
                    self.logger.warning(f"WSL detected but failed to find host IP: {e}")

        self.url = url
        self.logger.debug(
            f"AnkiConnectClient initialized with url={self.url} "
            f"(curl_bridge={self.use_windows_curl})"
        )

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT,
                limits=httpx.Limits(
                    max_connections=SYNC_CONCURRENCY,
                    max_keepalive_connections=SYNC_CONCURRENCY,
                ),
            )
        return self._client

    async def is_responsive(self) -> bool:
        """Check if AnkiConnect is reachable and has the expected API version."""
        try:
            payload = {"action": "version", "version": 6}
            data = await asyncio.wait_for(
                self._http_request(payload, timeout=RESPONSIVENESS_TIMEOUT),
                timeout=RESPONSIVENESS_TIMEOUT,
            )
            if isinstance(data, dict):
                return int(data.get("result") or 0) >= 6
            return False
        except Exception:
            return False

    async def invoke(self, action: str, **params: Any) -> Any:
        """Send one action and return its result; an error reply raises.

        A request that provably never left (no connection) is sent again, up to
        ``_ATTEMPTS`` times. A timeout is sent again only for an action ``_RESEND_SAFE``
        allows. For any other action, a timeout, a dropped connection or an unreadable
        reply raises ``OutcomeUnknown``: Anki may have applied it.
        """
        payload = {"action": action, "version": 6, "params": params}
        resend_safe = _is_resend_safe(action, params)
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                async with self._invoke_sem:
                    data = await asyncio.wait_for(
                        self._http_request(payload), timeout=REQUEST_TIMEOUT
                    )
            except _NOT_SENT as exc:
                self.logger.warning(
                    f"AnkiConnect '{action}' not sent: {exc} (attempt {attempt}/{_ATTEMPTS})"
                )
                if attempt == _ATTEMPTS:
                    raise
                await asyncio.sleep(_NOT_SENT_DELAY)
                continue
            except (TimeoutError, httpx.TimeoutException) as exc:
                self.logger.warning(
                    f"AnkiConnect timeout on '{action}' (attempt {attempt}/{_ATTEMPTS})"
                )
                # The client stays open: it is shared with the requests in flight beside
                # this one. The timed-out request's own connection closed when wait_for
                # cancelled it.
                if not resend_safe:
                    raise OutcomeUnknown(_unknown_text(action, "timed out")) from exc
                if attempt == _ATTEMPTS:
                    raise TimeoutError(
                        f"AnkiConnect timeout on '{action}' after {_ATTEMPTS} attempts"
                    ) from exc
            except Exception as e:
                self.logger.error(f"AnkiConnect call failed: {e}")
                if not resend_safe:
                    raise OutcomeUnknown(_unknown_text(action, f"failed in transit ({e})")) from e
                raise
            else:
                return self._result_of(action, data, resend_safe)
            await asyncio.sleep(_RETRY_DELAY)

    def _result_of(self, action: str, data: Any, resend_safe: bool) -> Any:
        """The result of a reply. An error reply raises; so does a malformed one."""
        problem = None
        if not isinstance(data, dict) or len(data) != 2:
            problem = "response has an unexpected number of fields"
        elif "error" not in data:
            problem = "response is missing required error field"
        elif "result" not in data:
            problem = "response is missing required result field"
        if problem is not None:
            self.logger.error(f"AnkiConnect call failed: {problem}")
            if not resend_safe:
                raise OutcomeUnknown(_unknown_text(action, problem))
            raise ValueError(problem)
        if data["error"] is not None:
            self.logger.error(f"AnkiConnect call failed: {data['error']}")
            raise Exception(data["error"])
        return data["result"]

    async def send_writes(self, writes: list[tuple[str, dict]], results: bool = False) -> list:
        """Send writes in one request, a lone one as itself; per write its error or None.

        With `results`, per write its result, or its error.
        """
        if len(writes) == 1:
            action, params = writes[0]
            try:
                value = await self.invoke(action, **params)
                return [value if results else None]
            except Exception as e:
                return [e]
        actions = [{"action": a, "version": 6, "params": p} for a, p in writes]
        try:
            replies = await self.invoke("multi", actions=actions)
            if not isinstance(replies, list) or len(replies) != len(writes):
                raise ValueError(f"multi answered {replies!r:.80} for {len(writes)} actions")
        except Exception as e:
            return [e] * len(writes)
        errors: list = []
        for reply in replies:
            if not isinstance(reply, dict) or set(reply) != {"result", "error"}:
                errors.append(ValueError(f"multi sub-reply is malformed: {reply!r:.80}"))
            elif reply["error"] is not None:
                errors.append(Exception(reply["error"]))
            else:
                errors.append(reply["result"] if results else None)
        return errors

    async def _http_request(self, payload: dict, *, timeout: float | None = None) -> dict:
        if self.use_windows_curl:
            effective_timeout = REQUEST_TIMEOUT if timeout is None else timeout
            cmd = [
                "curl.exe",
                "-fsS",
                "-X",
                "POST",
                self.url,
                "--max-time",
                str(effective_timeout),
                "-d",
                "@-",
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdin_data = json.dumps(payload).encode("utf-8")
            try:
                stdout, stderr = await proc.communicate(input=stdin_data)
            except BaseException:
                if proc.returncode is None:
                    with suppress(ProcessLookupError):
                        proc.kill()
                await proc.wait()
                raise
            if proc.returncode != 0:
                err_text = stderr.decode("utf-8", errors="replace").strip()
                if proc.returncode == 7:
                    raise httpx.ConnectError(f"curl.exe failed to connect: {err_text}")
                if proc.returncode == 28:
                    raise TimeoutError(f"curl.exe timed out: {err_text}")
                raise Exception(f"curl.exe failed: {err_text}")
            return json.loads(stdout.decode("utf-8"))
        client = self._ensure_client()
        if timeout is not None:
            resp = await client.post(self.url, json=payload, timeout=timeout)
        else:
            resp = await client.post(self.url, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
