"""Provider-reported usage: sniffing it, and reporting it.

The synthetic curve proves the request got smaller. Only the provider's own
usage block proves the prefix stayed warm (cache_read vs cache_creation) —
so the sniffer must read it out of both plain JSON and SSE responses without
ever holding up or altering a byte, and the ledger report must aggregate it
honestly.
"""
from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from burnless.proxy import ledger as ledger_mod
from burnless.proxy.server import SpineProxy, UsageSniffer, _make_handler

SSE_BODY = (
    b'event: message_start\n'
    b'data: {"type":"message_start","message":{"id":"m","usage":{"input_tokens":12,'
    b'"cache_read_input_tokens":8000,"cache_creation_input_tokens":150,"output_tokens":1}}}\n\n'
    b'event: content_block_delta\n'
    b'data: {"type":"content_block_delta","delta":{"text":"hello"}}\n\n'
    b'event: message_delta\n'
    b'data: {"type":"message_delta","usage":{"output_tokens":420}}\n\n'
)

JSON_BODY = json.dumps(
    {
        "id": "m",
        "content": [{"type": "text", "text": "ok"}],
        "usage": {
            "input_tokens": 30,
            "output_tokens": 77,
            "cache_read_input_tokens": 5000,
            "cache_creation_input_tokens": 0,
        },
    }
).encode()


# ---------------------------------------------------------------- sniffer


def test_sniffer_reads_sse_usage_taking_the_final_output_count():
    s = UsageSniffer()
    for i in range(0, len(SSE_BODY), 7):  # arbitrary chunk boundaries
        s.feed(SSE_BODY[i : i + 7])
    usage = s.usage()
    assert usage["cache_read_input_tokens"] == 8000
    assert usage["cache_creation_input_tokens"] == 150
    assert usage["output_tokens"] == 420  # message_delta wins over message_start


def test_sniffer_reads_plain_json_usage():
    s = UsageSniffer()
    s.feed(JSON_BODY)
    assert s.usage() == {
        "input_tokens": 30,
        "output_tokens": 77,
        "cache_read_input_tokens": 5000,
        "cache_creation_input_tokens": 0,
    }


def test_sniffer_is_bounded_and_survives_garbage():
    s = UsageSniffer()
    s.feed(b"\x00\xff not json at all " * 1000)
    assert s.usage() == {}
    big = UsageSniffer()
    big.feed(b'{"input_tokens": 5}' + b"x" * 500_000 + b'{"output_tokens": 9}')
    u = big.usage()  # head keeps the opening, tail keeps the close
    assert u.get("input_tokens") == 5 and u.get("output_tokens") == 9


# ---------------------------------------------------------------- report


def test_summarize_and_render(tmp_path):
    led = tmp_path / "ledger.jsonl"
    led.write_text(
        "\n".join(
            [
                json.dumps({"kind": "request", "applied": False, "reason": "below_regime", "bytes_in": 100}),
                json.dumps(
                    {"kind": "request", "applied": True, "absorbed": 3, "tokens_saved_approx": 900,
                     "bytes_in": 5000, "bytes_out": 2000}
                ),
                json.dumps({"kind": "usage", "input_tokens": 100, "output_tokens": 50,
                            "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0}),
                "not json at all",
            ]
        ),
        encoding="utf-8",
    )
    s = ledger_mod.summarize(led)
    assert s["requests"] == 2 and s["spine_applied"] == 1 and s["max_absorbed"] == 3
    assert s["passthrough_reasons"] == {"below_regime": 1}
    assert s["tokens_saved_approx"] == 900
    assert s["billed_prompt_tokens"] == 1000
    assert s["cache_hit_ratio"] == pytest.approx(0.9)
    out = ledger_mod.render(s)
    assert "90.0% of prompt" in out and "spine applied" in out


def test_summarize_missing_ledger_is_empty_not_error(tmp_path):
    s = ledger_mod.summarize(tmp_path / "nope.jsonl")
    assert s["requests"] == 0 and s["cache_hit_ratio"] is None
    assert "no provider usage recorded yet" in ledger_mod.render(s)


# ---------------------------------------------------------------- end to end


@pytest.mark.parametrize("body,ctype", [(SSE_BODY, "text/event-stream"), (JSON_BODY, "application/json")])
def test_usage_is_recorded_end_to_end_without_altering_bytes(tmp_path, body, ctype):
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    proxy = SpineProxy(tmp_path / "proxy", upstream=f"http://127.0.0.1:{up.server_address[1]}")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(proxy))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.server_address[1]}/v1/messages",
            data=json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as r:
            assert r.read() == body  # byte-identical passthrough
    finally:
        srv.shutdown()
        up.shutdown()

    s = ledger_mod.summarize(proxy.ledger)
    assert s["usage_rows"] == 1
    assert s["cache_read_input_tokens"] in (8000, 5000)
    assert s["cache_hit_ratio"] > 0.9  # the whole point: prompt served from cache
