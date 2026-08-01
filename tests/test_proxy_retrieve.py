"""Spine proxy recovery path: ref → capsule + verbatim exchange.

The doctrine under test (_design/SPINE_PROXY_2026-07-31.md §4): every capsule
ends in [ref:<hash>] and the verbatim original is always recoverable — via
resolve_ref (backs `burnless proxy show`) and via the proxy's loopback-only
GET /spine/<hash> endpoint. Lookups fail open (None / 404), never raise, and
the wire endpoint is refused to anything that is not 127.0.0.1/::1.
"""
from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from burnless.proxy import spine
from burnless.proxy.store import CapsuleStore, resolve_ref


def user(text: str) -> dict:
    return {"role": "user", "content": text}


def assistant(text: str) -> dict:
    return {"role": "assistant", "content": text}


def seed(tmp_path):
    """One stored exchange with its frozen capsule; returns (root, hash, exchange)."""
    root = tmp_path / "proxy"
    store = CapsuleStore(root)
    ex = [user("q " * 40), assistant("a " * 40)]
    h = spine.exchange_hash(ex)
    store.put_original(h, ex)
    store.put_capsule(h, f"U: q :: A: a [ref:{h}]")
    return root, h, ex


# ---------------------------------------------------------------- resolve_ref


def test_resolve_ref_roundtrip(tmp_path):
    root, h, ex = seed(tmp_path)
    record = resolve_ref(root, h)
    assert record == {"ref": h, "capsule": f"U: q :: A: a [ref:{h}]", "exchange": ex}


def test_resolve_ref_strips_ref_prefix(tmp_path):
    root, h, _ = seed(tmp_path)
    assert resolve_ref(root, f"ref:{h}") == resolve_ref(root, h)
    assert resolve_ref(root, f"  ref:{h}  ")["ref"] == h  # tolerate pasted whitespace


def test_resolve_ref_unknown_and_unsafe_are_none(tmp_path):
    root, _, _ = seed(tmp_path)
    assert resolve_ref(root, "deadbeef0000") is None  # well-formed but absent
    assert resolve_ref(root, "../../etc/passwd") is None
    assert resolve_ref(root, "") is None
    assert resolve_ref(root, None) is None
    assert resolve_ref(tmp_path / "nowhere", "deadbeef0000") is None  # no state dir


def test_resolve_ref_does_not_scaffold_state(tmp_path):
    missing = tmp_path / "nowhere"
    resolve_ref(missing, "deadbeef0000")
    assert not missing.exists()  # a lookup must not create directories


def test_resolve_ref_exchange_without_capsule_still_resolves(tmp_path):
    root = tmp_path / "proxy"
    store = CapsuleStore(root)
    ex = [user("only original"), assistant("no capsule yet")]
    h = spine.exchange_hash(ex)
    store.put_original(h, ex)
    record = resolve_ref(root, h)
    assert record["exchange"] == ex and record["capsule"] is None


# ---------------------------------------------------------------- /spine/ endpoint


@pytest.fixture
def live_proxy(tmp_path):
    pytest.importorskip("httpx")
    from burnless.proxy.server import SpineProxy, _make_handler

    root, h, ex = seed(tmp_path)
    proxy = SpineProxy(root, upstream="http://127.0.0.1:1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(proxy))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", h, ex
    finally:
        server.shutdown()
        server.server_close()


def test_spine_endpoint_returns_capsule_and_exchange(live_proxy):
    base, h, ex = live_proxy
    with urllib.request.urlopen(f"{base}/spine/{h}", timeout=10) as resp:
        assert resp.status == 200
        payload = json.loads(resp.read().decode("utf-8"))
    assert payload["ref"] == h
    assert payload["capsule"].endswith(f"[ref:{h}]")
    assert payload["exchange"] == ex


def test_spine_endpoint_unknown_hash_is_404(live_proxy):
    base, _, _ = live_proxy
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(f"{base}/spine/deadbeef0000", timeout=10)
    assert err.value.code == 404
    assert json.loads(err.value.read().decode("utf-8"))["error"]["type"] == "burnless_spine_not_found"


def test_spine_endpoint_traversal_shape_is_404_not_forward(live_proxy):
    base, _, _ = live_proxy
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(f"{base}/spine/not-a-hash!", timeout=10)
    assert err.value.code == 404  # answered locally, never proxied upstream


def test_spine_endpoint_refuses_non_loopback(tmp_path):
    """403 for any client that is not 127.0.0.1/::1, checked on the handler."""
    pytest.importorskip("httpx")
    from burnless.proxy.server import SpineProxy, _make_handler

    root, h, _ = seed(tmp_path)
    handler_cls = _make_handler(SpineProxy(root, upstream="http://127.0.0.1:1"))
    handler = object.__new__(handler_cls)  # no socket: exercise _spine directly
    handler.client_address = ("203.0.113.9", 50000)
    handler.command = "GET"
    handler.path = f"/spine/{h}"
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"GET /spine/{h} HTTP/1.1"
    handler.wfile = io.BytesIO()
    handler._spine()
    raw = handler.wfile.getvalue().decode("utf-8", "replace")
    assert raw.startswith("HTTP/1.0 403")
    assert "burnless_spine_forbidden" in raw


# ------------------------------------------------- unified retrieve (spine-aware)


from burnless import retrieve as retrieve_mod


def seed_bl(tmp_path):
    """A .burnless root whose proxy dir holds one frozen capsule + verbatim."""
    bl_root = tmp_path / ".burnless"
    store = CapsuleStore(bl_root / "proxy")
    ex = [user("fix the scoring bug in routing.py please"), assistant("done, sum not max")]
    h = spine.exchange_hash(ex)
    store.put_original(h, ex)
    store.put_capsule(h, f"U: fix scoring bug routing.py :: A: done, sum not max [ref:{h}]")
    return bl_root, h, ex


def test_search_finds_spine_capsules_by_text(tmp_path):
    bl_root, h, _ = seed_bl(tmp_path)
    results = retrieve_mod.search(bl_root, query="routing.py")
    assert any(r["ref_id"] == f"spine:{h}" and r["kind"] == "spine" for r in results)


def test_search_resolves_ref_shaped_queries_directly(tmp_path):
    bl_root, h, _ = seed_bl(tmp_path)
    for form in (h, f"ref:{h}", f"spine:{h}"):
        results = retrieve_mod.search(bl_root, query=form)
        assert [r["ref_id"] for r in results] == [f"spine:{h}"]
    assert retrieve_mod.search(bl_root, query="deadbeef0000") == []


def test_snippet_serves_spine_capsule_and_full_verbatim(tmp_path):
    bl_root, h, ex = seed_bl(tmp_path)
    short = retrieve_mod.snippet(bl_root, f"spine:{h}")
    assert f"[ref:{h}]" in short and "routing.py" in short
    full = retrieve_mod.snippet(bl_root, f"spine:{h}", full=True)
    assert json.dumps(ex[0]["content"], ensure_ascii=False)[1:-1] in full  # verbatim included
    assert retrieve_mod.snippet(bl_root, "spine:deadbeef0000") == ""


def test_search_without_proxy_dir_is_silent(tmp_path):
    bl_root = tmp_path / ".burnless"
    bl_root.mkdir()
    assert retrieve_mod.search(bl_root, query="anything") == []


def test_spine_records_do_not_pollute_capsule_kind_filter(tmp_path):
    bl_root, h, _ = seed_bl(tmp_path)
    results = retrieve_mod.search(bl_root, query="routing.py")
    assert all(r["kind"] != "capsule" for r in results if r["ref_id"].startswith("spine:"))
    assert all(r["raw_ref"] for r in results if r["kind"] == "spine")  # verbatim pointer present
