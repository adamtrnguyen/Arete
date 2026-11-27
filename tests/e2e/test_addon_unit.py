"""Unit tests for the bundled `arete_ankiconnect` add-on modules and actions."""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import importlib.util
import json
import socket
import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.addon_host import ADDON_DIR, AddonHost, load_addon_names


def _call(host: AddonHost, action: str, **params: Any) -> dict[str, Any]:
    return asyncio.run(host.http({"action": action, "version": 6, "params": params}))


def _ok(host: AddonHost, action: str, **params: Any) -> Any:
    reply = _call(host, action, **params)
    assert reply["error"] is None, f"{action}: {reply['error']}"
    return reply["result"]


def _seed(host: AddonHost, count: int = 2, deck: str = "Default") -> list[tuple[int, int]]:
    did = host.col.decks.id(deck)
    model = host.col.models.by_name("Basic")
    out: list[tuple[int, int]] = []
    for i in range(count):
        note = host.col.new_note(model)
        note["Front"] = f"front {i}"
        note["Back"] = f"back {i}"
        host.col.add_note(note, did)
        (cid,) = note.card_ids()
        out.append((int(note.id), int(cid)))
    return out


def _load_web_module(settings: dict[str, Any] | None = None) -> types.ModuleType:
    """Load `arete_ankiconnect/web.py` directly with a stub `util` package."""
    cfg = {
        "apiVersion": 6,
        "webBindAddress": "127.0.0.1",
        "webBindPort": 0,
        "webBacklog": 16,
        "webCorsOriginList": ["http://localhost"],
        "ignoreOriginList": [],
    }
    if settings:
        cfg.update(settings)

    pkg_name = "_test_addon_web_pkg"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(ADDON_DIR)]
    util_mod = types.ModuleType(f"{pkg_name}.util")
    util_mod.setting = lambda k: cfg[k]  # type: ignore[attr-defined]
    pkg.util = util_mod  # type: ignore[attr-defined]

    spec = importlib.util.spec_from_file_location(f"{pkg_name}.web", ADDON_DIR / "web.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    saved = {k: sys.modules.get(k) for k in (pkg_name, f"{pkg_name}.util", f"{pkg_name}.web")}
    sys.modules[pkg_name] = pkg
    sys.modules[f"{pkg_name}.util"] = util_mod
    sys.modules[f"{pkg_name}.web"] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod


def _load_util_module(addon_dir: Path, get_config_fn) -> tuple[types.ModuleType, dict[str, Any]]:
    """Load `arete_ankiconnect/util.py` with fake `aqt.mw.addonManager`."""
    fake_manager = types.SimpleNamespace(
        addonFromModule=lambda _name: "arete_ankiconnect",
        addonsFolder=lambda _addon: str(addon_dir),
        getConfig=get_config_fn,
    )
    fake_aqt = types.ModuleType("aqt")
    fake_aqt.mw = types.SimpleNamespace(addonManager=fake_manager)  # type: ignore[attr-defined]
    fake_anki = types.ModuleType("anki")
    fake_anki_sync = types.ModuleType("anki.sync")
    fake_anki.sync = fake_anki_sync  # type: ignore[attr-defined]

    mod_name = "_test_addon_util"
    spec = importlib.util.spec_from_file_location(mod_name, ADDON_DIR / "util.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)

    saved = {k: sys.modules.get(k) for k in ("aqt", "anki", "anki.sync", mod_name)}
    sys.modules["aqt"] = fake_aqt
    sys.modules["anki"] = fake_anki
    sys.modules["anki.sync"] = fake_anki_sync
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod, {"manager": fake_manager}


def _load_arete_module(
    fake_mw: Any,
    settings: dict[str, Any],
    warnings: list[str],
    tooltips: list[str],
    dialog_result: tuple[str, bool] = ("broken card", True),
    on_dialog_open=None,
) -> types.ModuleType:
    """Load `arete_ankiconnect/arete.py` with lightweight Qt/aqt stubs."""
    pkg_name = "_test_addon_arete_pkg"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(ADDON_DIR)]
    util_mod = types.ModuleType(f"{pkg_name}.util")
    util_mod.setting = lambda k: settings[k]  # type: ignore[attr-defined]
    pkg.util = util_mod  # type: ignore[attr-defined]

    clipboard_state: dict[str, str] = {}

    class FakeClipboard:
        def setText(self, text: str) -> None:
            clipboard_state["text"] = text

    class FakeQApplication:
        @staticmethod
        def clipboard():
            return FakeClipboard()

    class FakeQInputDialog:
        @staticmethod
        def getText(_parent, _title, _label):
            if on_dialog_open is not None:
                on_dialog_open()
            return dialog_result

    fake_aqt = types.ModuleType("aqt")
    fake_aqt.mw = fake_mw  # type: ignore[attr-defined]
    fake_aqt.gui_hooks = types.SimpleNamespace(state_did_change=[])  # type: ignore[attr-defined]

    fake_aqt_browser = types.ModuleType("aqt.browser")
    fake_aqt_browser.Browser = type("Browser", (), {})  # type: ignore[attr-defined]

    fake_aqt_qt = types.ModuleType("aqt.qt")
    fake_aqt_qt.QAction = type("QAction", (), {})  # type: ignore[attr-defined]
    fake_aqt_qt.QApplication = FakeQApplication  # type: ignore[attr-defined]
    fake_aqt_qt.QInputDialog = FakeQInputDialog  # type: ignore[attr-defined]
    fake_aqt_qt.QKeySequence = lambda seq: seq  # type: ignore[attr-defined]
    fake_aqt_qt.QMenu = type("QMenu", (), {})  # type: ignore[attr-defined]

    fake_aqt_utils = types.ModuleType("aqt.utils")
    fake_aqt_utils.showWarning = lambda msg: warnings.append(msg)  # type: ignore[attr-defined]
    fake_aqt_utils.tooltip = lambda msg: tooltips.append(msg)  # type: ignore[attr-defined]

    spec = importlib.util.spec_from_file_location(f"{pkg_name}.arete", ADDON_DIR / "arete.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    mod._clipboard_state = clipboard_state  # type: ignore[attr-defined]

    inject = {
        pkg_name: pkg,
        f"{pkg_name}.util": util_mod,
        f"{pkg_name}.arete": mod,
        "aqt": fake_aqt,
        "aqt.browser": fake_aqt_browser,
        "aqt.qt": fake_aqt_qt,
        "aqt.utils": fake_aqt_utils,
    }
    saved = {k: sys.modules.get(k) for k in inject}
    sys.modules.update(inject)
    try:
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod


# ---------------------------------------------------------------------------
# web.py tests
# ---------------------------------------------------------------------------


def test_web_request_is_frozen_dataclass():
    web = _load_web_module()
    req = web.WebRequest(b"POST", {b"host": b"localhost"}, b"{}")
    assert dataclasses.is_dataclass(req)
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.method = b"GET"


def test_web_parse_request_handles_no_space_and_colons_in_value():
    web = _load_web_module()
    client = web.WebClient(None, lambda _r: b"")
    body = b'{"action":"version"}'
    raw = (
        b"POST / HTTP/1.1\r\n"
        b"Host:localhost:8765\r\n"
        b"X-Custom: a: b: c\r\n" + f"Content-Length:{len(body)}\r\n\r\n".encode() + body
    )
    req, total = client.parseRequest(raw)
    assert req is not None
    assert total == len(raw)
    assert req.method == b"POST"
    assert req.headers[b"host"] == b"localhost:8765"
    assert req.headers[b"x-custom"] == b"a: b: c"
    assert req.body == body


def test_web_parse_request_rejects_bad_and_negative_content_length():
    web = _load_web_module()
    client = web.WebClient(None, lambda _r: b"")

    with pytest.raises(web._HttpParseError) as bad:
        client.parseRequest(b"POST / HTTP/1.1\r\nContent-Length: nope\r\n\r\n")
    assert bad.value.status == "400 Bad Request"

    with pytest.raises(web._HttpParseError) as neg:
        client.parseRequest(b"POST / HTTP/1.1\r\nContent-Length: -5\r\n\r\n")
    assert neg.value.status == "400 Bad Request"

    with pytest.raises(web._HttpParseError) as big:
        client.parseRequest(
            f"POST / HTTP/1.1\r\nContent-Length: {web.MAX_REQUEST_SIZE + 1}\r\n\r\n".encode()
        )
    assert big.value.status == "413 Payload Too Large"


def test_web_client_error_isolation_and_nonblocking_reads():
    web = _load_web_module()
    server = web.WebServer(lambda params: {"result": params["action"], "error": None})

    s1_a, s1_b = socket.socketpair()
    s2_a, s2_b = socket.socketpair()
    try:
        s1_a.setblocking(False)
        s2_a.setblocking(False)
        server.clients = [
            web.WebClient(s1_a, server.handlerWrapper),
            web.WebClient(s2_a, server.handlerWrapper),
        ]

        # Client 1 sends malformed Content-Length; Client 2 sends a valid JSON request.
        s1_b.sendall(b"POST / HTTP/1.1\r\nContent-Length: bad\r\n\r\n")
        body = b'{"action": "version", "version": 6}'
        s2_b.sendall(
            b"POST / HTTP/1.1\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )

        server.advanceClients()
        assert server.clients == []

        resp1 = s1_b.recv(4096)
        resp2 = s2_b.recv(4096)
        assert b"HTTP/1.1 400 Bad Request" in resp1
        assert b"Connection: close" in resp1
        assert b"HTTP/1.1 200 OK" in resp2
        assert b'"result": "version"' in resp2
    finally:
        for sock in (s1_a, s1_b, s2_a, s2_b):
            sock.close()


def test_web_client_idle_timeout_closes_zombie_socket():
    web = _load_web_module()
    s_a, s_b = socket.socketpair()
    try:
        s_a.setblocking(False)
        client = web.WebClient(s_a, lambda _r: b"")
        client.lastActivity = time.monotonic() - (web.CLIENT_IDLE_TIMEOUT_S + 1.0)
        assert client.advance() is False
        assert client.sock is None
    finally:
        s_a.close()
        s_b.close()


def test_web_cors_and_origin_sanitization():
    web = _load_web_module()
    server = web.WebServer(lambda _p: {"result": "ok", "error": None})

    # Localhost and 127.0.0.1 HTTP/HTTPS with ports allowed when http://localhost is configured
    for origin in (
        b"http://localhost",
        b"https://localhost:8443",
        b"http://127.0.0.1:8765",
        b"https://127.0.0.1:8443",
    ):
        allowed, cors = server.allowOrigin(web.WebRequest(b"POST", {b"origin": origin}, b""))
        assert allowed is True
        assert cors == origin.decode()

    # Browser extensions are NOT blindly allowed by default
    for ext in (b"chrome-extension://abc", b"moz-extension://def", b"safari-web-extension://ghi"):
        allowed, _ = server.allowOrigin(web.WebRequest(b"POST", {b"origin": ext}, b""))
        assert allowed is False

    # Non-UTF-8 origin is safely denied without crashing
    allowed, _ = server.allowOrigin(web.WebRequest(b"POST", {b"origin": b"\xff\xfe"}, b""))
    assert allowed is False

    # Bare CR/LF inside Origin header is stripped from reflected ACAO on requestPermission
    req = web.WebRequest(
        b"POST",
        {b"origin": b"http://evil.test\rX-Injected: yes"},
        b'{"action": "requestPermission", "version": 6}',
    )
    resp = server.handlerWrapper(req)
    assert b"\rX-Injected" not in resp
    assert b"Access-Control-Allow-Origin: http://evil.testX-Injected: yes\r\n" in resp


def test_web_request_validation():
    web = _load_web_module()
    web._validate_request({"action": "version", "version": 6, "params": {}})
    for bad in (
        [],
        {},
        {"action": ""},
        {"action": 5},
        {"action": "version", "version": True},
        {"action": "version", "params": []},
    ):
        with pytest.raises(ValueError):
            web._validate_request(bad)


# ---------------------------------------------------------------------------
# util.py tests
# ---------------------------------------------------------------------------


def test_util_setting_handles_none_config_defaults_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    current_cfg: list[Any] = [None]
    util_mod, _ = _load_util_module(tmp_path, lambda _name: current_cfg[0])

    # None from addonManager.getConfig falls back cleanly to DEFAULT_CONFIG
    assert util_mod.setting("webBindPort") == 8765
    assert util_mod.setting("vault_root") == ""

    # Mutating a returned list does not corrupt the cache
    cors = util_mod.setting("webCorsOriginList")
    cors.append("http://mutated")
    assert "http://mutated" not in util_mod.setting("webCorsOriginList")

    # Updating meta.json changes stamp and reloads config
    current_cfg[0] = {"vault_root": "/tmp/my-vault"}
    (tmp_path / "meta.json").write_text(
        '{"config": {"vault_root": "/tmp/my-vault"}}', encoding="utf-8"
    )
    assert util_mod.setting("vault_root") == "/tmp/my-vault"

    # Env overrides & invalid port error
    monkeypatch.setenv("ANKICONNECT_BIND_PORT", "9999")
    assert util_mod.setting("webBindPort") == 9999
    monkeypatch.setenv("ANKICONNECT_BIND_PORT", "not-a-port")
    with pytest.raises(ValueError, match="ANKICONNECT_BIND_PORT"):
        util_mod.setting("webBindPort")
    monkeypatch.delenv("ANKICONNECT_BIND_PORT")

    with pytest.raises(Exception, match="setting noSuchKey not found"):
        util_mod.setting("noSuchKey")


# ---------------------------------------------------------------------------
# arete.py tests
# ---------------------------------------------------------------------------


def test_arete_report_atomic_and_refuses_corrupt_files(tmp_path: Path):
    warnings: list[str] = []
    tooltips: list[str] = []
    mod = _load_arete_module(types.SimpleNamespace(), {"vault_root": ""}, warnings, tooltips)

    reports_file = tmp_path / "reports.json"
    assert mod._append_report_atomic({"cid": 1, "note": "typo"}, str(reports_file)) is True
    assert json.loads(reports_file.read_text(encoding="utf-8")) == [{"cid": 1, "note": "typo"}]

    # Corrupt JSON is NOT overwritten
    reports_file.write_text("{not valid json", encoding="utf-8")
    assert mod._append_report_atomic({"cid": 2, "note": "x"}, str(reports_file)) is False
    assert reports_file.read_text(encoding="utf-8") == "{not valid json"
    assert warnings and "Cannot read" in warnings[-1]

    # Valid non-list JSON is NOT overwritten
    reports_file.write_text('{"unexpected": true}', encoding="utf-8")
    assert mod._append_report_atomic({"cid": 3, "note": "y"}, str(reports_file)) is False
    assert reports_file.read_text(encoding="utf-8") == '{"unexpected": true}'
    assert "does not contain a JSON list" in warnings[-1]


def test_arete_report_current_card_suspends_captured_card_even_if_reviewer_changes(tmp_path: Path):
    suspended: list[list[int]] = []
    next_calls: list[bool] = []

    class FakeNote(dict):
        def __init__(self, nid: int, src: str):
            super().__init__(_obsidian_source=src)
            self.id = nid
            self.fields = ["<b>Front question</b>"]

    card_a = types.SimpleNamespace(id=101, note=lambda: FakeNote(11, "Vault|a.md|5|arete_A"))
    card_b = types.SimpleNamespace(id=202, note=lambda: FakeNote(22, "Vault|b.md|9|arete_B"))
    reviewer = types.SimpleNamespace(card=card_a, nextCard=lambda: next_calls.append(True))
    fake_col = types.SimpleNamespace(
        sched=types.SimpleNamespace(suspend_cards=lambda cids: suspended.append(list(cids)))
    )
    fake_mw = types.SimpleNamespace(reviewer=reviewer, col=fake_col)

    def swap_card_during_dialog():
        # Simulate mw.reset() swapping reviewer.card while modal dialog is open
        reviewer.card = card_b

    warnings: list[str] = []
    tooltips: list[str] = []
    mod = _load_arete_module(
        fake_mw,
        {"vault_root": "/my/vault"},
        warnings,
        tooltips,
        dialog_result=("bad wording", True),
        on_dialog_open=swap_card_during_dialog,
    )
    mod.REPORTS_PATH = str(tmp_path / "reports.json")

    mod.report_current_card()

    assert warnings == []
    assert suspended == [[101]]  # suspended original card_a, NOT swapped card_b!
    assert next_calls == []  # did NOT advance past card_b!
    saved = json.loads((tmp_path / "reports.json").read_text(encoding="utf-8"))
    assert len(saved) == 1 and saved[0]["cid"] == 101 and saved[0]["front"] == "Front question"

    # copy_obsidian_source reads vault_root dynamically via util.setting
    reviewer.card = card_a
    mod.copy_obsidian_source()
    assert mod._clipboard_state["text"] == "/my/vault/a.md (card: arete_A)"


# ---------------------------------------------------------------------------
# AddonHost action unit tests (real Anki 26.8.1 collection)
# ---------------------------------------------------------------------------


@pytest.fixture
def host(tmp_path: Path):
    h = AddonHost(tmp_path / "collection.anki2")
    yield h
    h.close()


def test_set_home_deck_rejects_filtered_deck_and_validates_cids(host: AddonHost):
    (_, cid_a), (_, cid_b) = _seed(host, count=2, deck="Home")
    _ok(host, "createDeck", deck="Target")
    _ok(host, "createFilteredDeck", name="Arete::Queue", cids=[cid_a])

    # Rejects filtered deck as home deck
    err = _call(host, "setHomeDeck", cards=[cid_b], deck="Arete::Queue")
    assert err["error"] is not None and "filtered deck" in err["error"]

    # Rejects non-int and missing card IDs
    assert _call(host, "setHomeDeck", cards=["not-int"], deck="Target")["error"] is not None
    assert _call(host, "setHomeDeck", cards=[999999999], deck="Target")["error"] is not None

    # Moves normal card & updates queued card's odid without removing from filtered deck
    refreshes: list[bool] = []
    host._addon.scheduleRefresh = lambda: refreshes.append(True)
    _ok(host, "setHomeDeck", cards=[cid_a, cid_b], deck="Target")
    assert len(refreshes) == 1
    assert _ok(host, "getHomeDecks", cards=[cid_a, cid_b]) == [
        {"homeDeck": "Target", "filteredDeck": "Arete::Queue"},
        {"homeDeck": "Target", "filteredDeck": None},
    ]

    # No-op move (already in Target) does not trigger scheduleRefresh
    refreshes.clear()
    _ok(host, "setHomeDeck", cards=[cid_a, cid_b], deck="Target")
    assert refreshes == []


def test_get_model_info_returns_canonical_name(host: AddonHost):
    info = _ok(host, "getModelInfo", models=["basic", "CLOZE", "NoSuchModel"])
    assert info["basic"]["name"] == "Basic"
    assert info["basic"]["type"] == 0
    assert info["CLOZE"]["name"] == "Cloze"
    assert info["CLOZE"]["type"] == 1
    assert info["NoSuchModel"] is None


def test_suspend_does_not_skip_elements_on_mutation(host: AddonHost):
    (_, cid_a), (_, cid_b) = _seed(host, count=2)
    assert _ok(host, "suspend", cards=[cid_a, cid_b]) is True
    # Both already suspended -> must return False (previously returned True due to list.remove!)
    assert _ok(host, "suspend", cards=[cid_a, cid_b]) is False
    # Missing card right after an already-suspended card must still raise NotFoundError!
    missing = _call(host, "suspend", cards=[cid_a, 9999999999])
    assert missing["error"] == "Card was not found: 9999999999"
    # Wire contract preserved: unsuspend returns None while still unsuspending
    assert _ok(host, "unsuspend", cards=[cid_a, cid_b]) is None
    assert _ok(host, "areSuspended", cards=[cid_a, cid_b]) == [False, False]


def test_add_note_cleans_up_zero_card_orphan(host: AddonHost):
    before = host.col.db.scalar("select count() from notes")

    # Simulate a note whose templates produce 0 cards after col.add_note persists the note row.
    real_add_note = host.col.add_note

    def add_note_zero_cards(note, deck_id):
        res = real_add_note(note, deck_id)
        host.col.db.execute("delete from cards where nid = ?", note.id)
        return res

    host.col.add_note = add_note_zero_cards  # type: ignore[method-assign]
    try:
        reply = _call(
            host,
            "addNote",
            note={
                "deckName": "Default",
                "modelName": "Basic",
                "fields": {"Front": "orphan q", "Back": "orphan a"},
            },
        )
    finally:
        host.col.add_note = real_add_note  # type: ignore[method-assign]

    assert reply["error"] is not None and "empty question" in reply["error"]
    assert host.col.db.scalar("select count() from notes") == before


def test_insert_reviews_parameterized_and_get_reviews_supports_optional_data_column(
    host: AddonHost,
):
    (_, cid_a), _ = _seed(host, count=2)

    # SQL injection payload is rejected
    bad = _call(
        host,
        "insertReviews",
        reviews=[[1, cid_a, 0, 3, 1, 0, 2500, 1000, "0); DROP TABLE notes;--"]],
    )
    assert bad["error"] is not None
    assert _call(host, "insertReviews", reviews=[[1, 2, 3]])["error"] is not None

    # Valid 9-int review row inserts cleanly and is returned by getReviewsOfCards
    _ok(host, "insertReviews", reviews=[[1700000000000, cid_a, -1, 3, 1, 0, 2500, 4200, 0]])
    reviews = _ok(host, "getReviewsOfCards", cards=[cid_a])
    assert len(reviews[cid_a]) == 1
    assert reviews[cid_a][0]["time"] == 4200
    assert "data" not in reviews[cid_a][0]

    # When revlog has a `data` column, getReviewsOfCards includes it consistently
    host.col.db.execute("alter table revlog add column data text not null default ''")
    host.col.db.execute("update revlog set data = '{\"s\":1.5}' where id = 1700000000000")
    reviews_with_data = _ok(host, "getReviewsOfCards", cards=[cid_a])
    assert reviews_with_data[cid_a][0]["data"] == '{"s":1.5}'


def test_media_actions_block_path_traversal(host: AddonHost, tmp_path: Path):
    # Bad filenames rejected
    assert (
        _call(host, "storeMediaFile", filename="../evil.txt", data=base64.b64encode(b"x").decode())[
            "error"
        ]
        is not None
    )
    assert _call(host, "deleteMediaFile", filename="../evil.txt")["error"] is not None

    # Arbitrary path outside allowed roots and `..` traversal rejected
    assert _call(host, "storeMediaFile", filename="ok.txt", path="/etc/passwd")["error"] is not None
    assert (
        _call(host, "storeMediaFile", filename="ok.txt", path=str(tmp_path / ".." / "x"))["error"]
        is not None
    )

    # Valid path inside tmp_path (under tempfile.gettempdir()) succeeds
    sample = tmp_path / "sample.txt"
    sample.write_bytes(b"hello")
    assert _ok(host, "storeMediaFile", filename="sample.txt", path=str(sample)) == "sample.txt"
    assert _ok(host, "getMediaFilesNames", pattern="*.txt") == ["sample.txt"]

    # Pattern traversal rejected
    assert _call(host, "getMediaFilesNames", pattern="../../etc/*")["error"] is not None
    assert _call(host, "getMediaFilesNames", pattern="/etc/*")["error"] is not None


def test_create_filtered_deck_validation_and_learning_card_safety(host: AddonHost):
    (_, cid_a), (_, cid_b) = _seed(host, count=2)
    assert _call(host, "createFilteredDeck", name="Q", cids=["1 OR is:due"])["error"] is not None
    assert _call(host, "createFilteredDeck", name="Q", cids=[True])["error"] is not None

    # Make cid_a look like an intraday learning card (queue=1, due=unix timestamp)
    card_a = host.col.get_card(cid_a)
    card_a.type = 1
    card_a.queue = 1
    card_a.due = 1800000000
    host.col.update_card(card_a, skip_undo_entry=True)

    did = _ok(host, "createFilteredDeck", name="Arete::Queue", cids=[cid_a, cid_b])
    assert isinstance(did, int)
    after_a = host.col.get_card(cid_a)
    after_b = host.col.get_card(cid_b)
    if after_a.did == did and after_a.queue == 1:
        assert after_a.due != 1000
    assert after_b.did == did and after_b.due == 1001


def test_refresh_called_for_delete_retype_and_model_field_add(host: AddonHost):
    (nid_a, _), _ = _seed(host, count=2)
    refreshes: list[bool] = []
    host._addon.scheduleRefresh = lambda: refreshes.append(True)

    # modelFieldAdd refreshes only when modified
    _ok(host, "modelFieldAdd", modelName="Basic", fieldName="Front")
    assert refreshes == []
    _ok(host, "modelFieldAdd", modelName="Basic", fieldName="_obsidian_source")
    assert len(refreshes) == 1

    # changeNoteType validates empty / length, short-circuits same-model no-op, refreshes on change
    assert (
        _call(host, "changeNoteType", notes=[], modelName="Basic", newFields=[0, 1, 2])["error"]
        is not None
    )
    assert (
        _call(host, "changeNoteType", notes=[nid_a], modelName="Basic", newFields=[0])["error"]
        is not None
    )
    refreshes.clear()
    assert (
        _ok(host, "changeNoteType", notes=[nid_a], modelName="Basic", newFields=[0, 1, 2]) is True
    )
    assert refreshes == []

    _ok(
        host,
        "createModel",
        modelName="Basic2",
        inOrderFields=["Front", "Back", "_obsidian_source"],
        cardTemplates=[{"Front": "{{Front}}", "Back": "{{Back}}"}],
    )
    refreshes.clear()
    assert (
        _ok(host, "changeNoteType", notes=[nid_a], modelName="Basic2", newFields=[0, 1, 2]) is True
    )
    assert len(refreshes) == 1

    # deleteNotes triggers scheduleRefresh
    refreshes.clear()
    _ok(host, "deleteNotes", notes=[nid_a])
    assert len(refreshes) == 1


def test_fixed_upstream_actions_and_edge_cases(host: AddonHost):
    (_, cid_a), (_, cid_b) = _seed(host, count=2)

    # saveDeckConfig and setDeckConfigId work on Anki 26.8.1
    cfg = _ok(host, "getDeckConfig", deck="Default")
    assert _ok(host, "saveDeckConfig", config=cfg) is True
    cloned_id = _ok(host, "cloneDeckConfigId", name="Custom")
    _ok(host, "createDeck", deck="DeckA")
    assert _ok(host, "setDeckConfigId", decks=["DeckA"], configId=cloned_id) is True
    assert _ok(host, "getDeckConfig", deck="DeckA")["id"] == cloned_id

    # modelTemplateAdd saves updates when template already exists
    _ok(
        host,
        "modelTemplateAdd",
        modelName="Basic",
        template={"Name": "Card 1", "Front": "{{Front}} updated", "Back": "{{Back}}"},
    )
    assert _ok(host, "modelTemplates", modelName="Basic")["Card 1"]["Front"] == "{{Front}} updated"

    # apiReflect skips non-string entries without crashing
    reflected = _ok(host, "apiReflect", scopes=["actions"], actions=["version", 123, None])
    assert reflected == {"scopes": ["actions"], "actions": ["version"]}

    # getDecks ignores missing card IDs; getHomeDecks handles unhashable items
    assert _ok(host, "getDecks", cards=[cid_a, 9999999999]) == {"Default": [cid_a]}
    assert _ok(host, "getHomeDecks", cards=[cid_a, ["unhashable"]]) == [
        {"homeDeck": "Default", "filteredDeck": None},
        None,
    ]

    # getFSRSStats handles invalid CIDs per-item without aborting batch
    fsrs = _ok(host, "getFSRSStats", cards=["bad", cid_a])
    assert "error" in fsrs[0] and fsrs[1]["cardId"] == cid_a

    # areDue & getIntervals do not crash on non-new card with empty revlog
    card_b = host.col.get_card(cid_b)
    card_b.type = 2
    card_b.queue = 2
    host.col.update_card(card_b, skip_undo_entry=True)
    assert isinstance(_ok(host, "areDue", cards=[cid_b])[0], bool)
    assert _ok(host, "getIntervals", cards=[cid_b]) == [0]

    # forgetCards & relearnCards run cleanly via Anki 26 APIs
    _ok(host, "relearnCards", cards=[cid_b])
    assert host.col.get_card(cid_b).queue == 1
    _ok(host, "forgetCards", cards=[cid_b])
    assert host.col.get_card(cid_b).queue == 0


def test_parse_anki_version_handles_prerelease_suffixes():
    import re as re_mod

    ns = load_addon_names("__init__.py", ["_parse_anki_version"], {"re": re_mod})
    parse = ns["_parse_anki_version"]
    assert parse("26.8.1") == (26, 8, 1)
    assert parse("26.8.1rc1") == (26, 8, 1)
    assert parse("26.8.1-beta2") == (26, 8, 1)
