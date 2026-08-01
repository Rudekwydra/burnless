"""Spine proxy server end-to-end: real ThreadingHTTPServer instances.

Everything here runs against a real proxy server (SpineProxy + _make_handler)
in front of a real fake-upstream server, both bound to port 0 on loopback.
The invariants under test are the server-level ones the unit tests in
test_proxy_spine.py cannot see: SSE byte-fidelity through the forward loop,
the rolling cycle over live turns with the background compressor, concurrent
conversations sharing one proxy (and one ledger), the 502 fail path when the
upstream is gone, and transparent forwarding of non-messages traffic.
"""
from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

httpx = pytest.importorskip("httpx")

from burnless.proxy import spine
from burnless.proxy.server import SpineProxy, _make_handler

# Big enough that a capsule always beats the verbatim exchange by a wide margin.
FILLER_Q = "lorem ipsum " * 200
FILLER_A = "dolor sit amet " * 200


# ---------------------------------------------------------------- harness


@contextlib.contextmanager
def serving(handler_cls):
    """A real ThreadingHTTPServer on a random free port (bind 0, read back)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def upstream_handler(respond, seen: list):
    """Fake upstream: records every request into `seen`, then calls respond."""

    class Upstream(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, fmt, *args):
            pass

        def _serve(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            seen.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                }
            )
            respond(self, body)

        do_GET = _serve
        do_POST = _serve

    return Upstream


@contextlib.contextmanager
def proxied(tmp_path, respond, **proxy_kwargs):
    """Fake upstream + SpineProxy server in front of it. Yields (base, seen, proxy)."""
    seen: list = []
    with serving(upstream_handler(respond, seen)) as up_port:
        proxy = SpineProxy(
            tmp_path / "proxy", upstream=f"http://127.0.0.1:{up_port}", **proxy_kwargs
        )
        try:
            with serving(_make_handler(proxy)) as px_port:
                yield f"http://127.0.0.1:{px_port}", seen, proxy
        finally:
            proxy.client.close()


def json_reply(handler, payload, status=200):
    data = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def wait_idle(proxy, timeout=5.0):
    """Poll until the background compressor has frozen every queued capsule."""
    deadline = time.time() + timeout
    while proxy.store.pending() and time.time() < deadline:
        time.sleep(0.01)
    assert proxy.store.pending() == [], "compressor did not drain in time"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- a. SSE


SSE_EVENTS = [
    b'event: message_start\ndata: {"type": "message_start", "message": {"id": "msg_e2e"}}\n\n',
    b'event: content_block_start\ndata: {"type": "content_block_start", "index": 0}\n\n',
    b'event: content_block_delta\ndata: {"type": "content_block_delta", "delta": {"text": "hello '
    + b"x" * 300
    + b'"}}\n\n',
    b'event: content_block_delta\ndata: {"type": "content_block_delta", "delta": {"text": "world"}}\n\n',
    b'event: content_block_stop\ndata: {"type": "content_block_stop", "index": 0}\n\n',
    b'event: message_stop\ndata: {"type": "message_stop"}\n\n',
]


def test_sse_streaming_passthrough_byte_fidelity(tmp_path):
    def respond(handler, body):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.end_headers()
        for chunk in SSE_EVENTS:
            handler.wfile.write(chunk)
            handler.wfile.flush()
            time.sleep(0.03)  # real inter-event gaps: the proxy must stream, not buffer

    with proxied(tmp_path, respond) as (base, seen, proxy):
        received = b""
        with httpx.stream(
            "POST",
            base + "/v1/messages",
            json={"model": "m", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
            timeout=15,
        ) as r:
            assert r.status_code == 200
            assert r.headers["content-type"] == "text/event-stream"
            for chunk in r.iter_raw():
                received += chunk
    # every byte, in order — the exact concatenation the upstream wrote
    assert received == b"".join(SSE_EVENTS)
    assert seen[0]["method"] == "POST" and seen[0]["path"] == "/v1/messages"


# ---------------------------------------------------------------- b. rolling cycle


def test_rolling_cycle_absorbs_in_batches(tmp_path):
    """Client resends full verbatim history each turn, like Claude Code does.

    The spine engages on batch boundaries, not every turn: absorbing one
    capsule per turn measurably costs more against a real provider, because
    the capsule lands before the verbatim tail and forces the whole tail to be
    cache-written again. So the shape to assert is a staircase — the upstream
    count drops when a batch lands, and between batches the request only
    grows by the new exchange (pure append, cache-friendly).
    """

    def respond(handler, body):
        n = len(json.loads(body)["messages"])
        json_reply(
            handler,
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": f"answer {n}: " + FILLER_A}],
            },
        )

    with proxied(tmp_path, respond, min_exchanges=3, keep_tail_exchanges=1) as (
        base,
        seen,
        proxy,
    ):
        history: list = []
        client_counts: list[int] = []
        upstream_counts: list[int] = []
        for turn in range(1, 13):
            history.append({"role": "user", "content": f"question {turn}: " + FILLER_Q})
            client_counts.append(len(history))
            r = httpx.post(
                base + "/v1/messages",
                json={"model": "m", "max_tokens": 64, "messages": history},
                timeout=30,
            )
            assert r.status_code == 200
            history.append({"role": "assistant", "content": r.json()["content"][0]["text"]})
            upstream_counts.append(len(json.loads(seen[-1]["body"])["messages"]))
            wait_idle(proxy)  # think time: the compressor freezes this turn's capsule

        # early turns: pure passthrough, upstream sees exactly what the client sent
        assert upstream_counts[:3] == client_counts[:3]
        # a batch landed: at least once the upstream count DROPPED while the
        # client's history kept growing — that is the spine absorbing
        drops = [i for i in range(1, len(upstream_counts))
                 if upstream_counts[i] < upstream_counts[i - 1]]
        assert drops, (client_counts, upstream_counts)
        # and it stays ahead: the client carries more than the model receives
        assert client_counts[-1] > upstream_counts[-1]
        # between batches the request grows by exactly one exchange (append only)
        after = drops[-1]
        for i in range(after + 1, len(upstream_counts)):
            assert upstream_counts[i] - upstream_counts[i - 1] == 2, upstream_counts

        # the spine user message is a block list; cache_control rides the LAST block
        last = json.loads(seen[-1]["body"])["messages"]
        assert last[0]["role"] == "user"
        blocks = last[0]["content"]
        assert isinstance(blocks, list) and len(blocks) >= 2
        assert blocks[0]["text"] == spine.SPINE_HEADER
        assert blocks[-1].get("cache_control") == {"type": "ephemeral"}
        assert all("cache_control" not in b for b in blocks[:-1])
        assert last[1] == {"role": "assistant", "content": spine.SPINE_ACK}
        # tail region (the current prompt) reaches the upstream verbatim
        assert last[-1] == history[-2]


# ---------------------------------------------------------------- c. concurrency


def test_concurrent_conversations_and_valid_ledger(tmp_path):
    def respond(handler, body):
        tail = json.loads(body)["messages"][-1]["content"]
        marker = tail.split(":")[0] if isinstance(tail, str) else "?"
        json_reply(handler, {"type": "message", "content": [{"type": "text", "text": f"ack {marker}"}]})

    n_threads, n_turns = 8, 5
    with proxied(tmp_path, respond) as (base, seen, proxy):
        errors: list[str] = []

        def worker(t: int) -> None:
            try:
                history: list = []
                for k in range(n_turns):
                    history.append(
                        {"role": "user", "content": f"thread-{t}-turn-{k}: " + f"pad{t} " * 80}
                    )
                    r = httpx.post(
                        base + "/v1/messages",
                        json={"model": "m", "messages": history},
                        timeout=30,
                    )
                    assert r.status_code == 200
                    text = r.json()["content"][0]["text"]
                    assert f"thread-{t}-turn-{k}" in text, f"crossed wires: {text!r}"
                    history.append(
                        {"role": "assistant", "content": f"re {t}-{k}: " + f"resp{t} " * 80}
                    )
            except Exception as exc:
                errors.append(f"thread {t}: {exc!r}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=25)
        assert errors == []
        assert len(seen) == n_threads * n_turns

        # ledger stays valid JSONL under concurrent writes: one object per line
        lines = proxy.ledger.read_text(encoding="utf-8").splitlines()
        assert len(lines) == n_threads * n_turns
        for line in lines:
            entry = json.loads(line)
            assert isinstance(entry, dict) and "ts" in entry


# ---------------------------------------------------------------- d. upstream down


def test_upstream_down_yields_502_json_error(tmp_path):
    port = free_port()  # nothing listens here
    proxy = SpineProxy(tmp_path / "proxy", upstream=f"http://127.0.0.1:{port}")
    try:
        with serving(_make_handler(proxy)) as px_port:
            r = httpx.post(
                f"http://127.0.0.1:{px_port}/v1/messages",
                json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                timeout=10,
            )
        assert r.status_code == 502
        assert r.json()["error"]["type"] == "burnless_proxy_upstream"
    finally:
        proxy.client.close()


# ---------------------------------------------------------------- e. non-messages


def test_non_messages_get_forwards_untouched(tmp_path):
    payload = {"data": [{"id": "claude-sonnet-4-5"}], "has_more": False}

    def respond(handler, body):
        json_reply(handler, payload)

    with proxied(tmp_path, respond) as (base, seen, proxy):
        r = httpx.get(
            base + "/v1/models?limit=5",
            headers={"x-api-key": "sk-test-e2e", "anthropic-version": "2023-06-01"},
            timeout=10,
        )
    assert r.status_code == 200
    assert r.json() == payload
    req = seen[0]
    assert req["method"] == "GET"
    assert req["path"] == "/v1/models?limit=5"  # path and query survive verbatim
    assert req["body"] == b""
    assert req["headers"]["x-api-key"] == "sk-test-e2e"
    assert req["headers"]["anthropic-version"] == "2023-06-01"
    assert not proxy.ledger.exists()  # nothing to record on the non-messages path
