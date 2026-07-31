"""Minimal Anthropic-compatible reverse proxy carrying the capsule spine.

Point ANTHROPIC_BASE_URL at it. Only POST …/v1/messages is rewritten (the
spine transform) and GET /spine/<hash> is answered locally (loopback-only
capsule recovery); every other request is forwarded untouched. Responses —
including SSE streams — are passed through byte by byte. Any failure inside
the transform forwards the original request: the proxy must never be the
reason a conversation breaks.

httpx is guaranteed present (hard dependency of the anthropic SDK).
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from . import spine
from .compressor import CompressorThread
from .store import CapsuleStore, resolve_ref

_HOP_HEADERS = {
    "host", "content-length", "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade",
}


class SpineProxy:
    def __init__(
        self,
        root: Path,
        *,
        upstream: str = "https://api.anthropic.com",
        keep_tail_exchanges: int = 1,
        min_exchanges: int = 3,
        codec: str = "extractive",
        max_capsule_chars: int = 700,
        cache_breakpoint: bool = True,
        raw_retention: str = "plain",
    ):
        self.upstream = upstream.rstrip("/")
        self.root = Path(root)
        self.store = CapsuleStore(self.root, raw_retention=raw_retention)
        self.ledger = self.root / "ledger.jsonl"
        self.keep_tail_exchanges = keep_tail_exchanges
        self.min_exchanges = min_exchanges
        self.cache_breakpoint = cache_breakpoint
        self.compressor = CompressorThread(self.store, codec=codec, max_chars=max_capsule_chars)
        self.compressor.start()
        self.compressor.enqueue(self.store.pending())  # drain the restart backlog
        self.client = httpx.Client(timeout=httpx.Timeout(600.0, connect=15.0))
        self._ledger_lock = threading.Lock()

    def rewrite(self, path: str, body: bytes) -> tuple[bytes, dict]:
        """Apply the spine transform when the request is a messages call."""
        route = path.split("?", 1)[0].rstrip("/")  # SDKs post /v1/messages?beta=true
        if not route.endswith("/v1/messages"):
            return body, {"applied": False, "reason": "not_messages"}
        try:
            parsed = json.loads(body)
        except Exception:
            return body, {"applied": False, "reason": "unparseable"}
        new_body, pending, stats = spine.transform(
            parsed,
            self.store,
            keep_tail_exchanges=self.keep_tail_exchanges,
            min_exchanges=self.min_exchanges,
            cache_breakpoint=self.cache_breakpoint,
        )
        if pending:
            self.compressor.enqueue(pending)
        stats["bytes_in"] = len(body)  # every ledger row carries the baseline
        if not stats.get("applied"):
            return body, stats
        out = json.dumps(new_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        stats["bytes_out"] = len(out)
        return out, stats

    def record(self, stats: dict) -> None:
        try:
            line = json.dumps({"ts": round(time.time(), 3), **stats}, ensure_ascii=False)
            with self._ledger_lock, open(self.ledger, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def _make_handler(proxy: SpineProxy):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"  # close-delimited responses: simplest correct streaming

        def log_message(self, fmt, *args):  # quiet; the ledger is the record
            pass

        def _forward(self, body: bytes | None) -> None:
            headers = {
                k: v for k, v in self.headers.items() if k.lower() not in _HOP_HEADERS
            }
            url = proxy.upstream + self.path
            sent_headers = False
            try:
                with proxy.client.stream(
                    self.command, url, headers=headers, content=body
                ) as upstream:
                    self.send_response(upstream.status_code)
                    sent_headers = True
                    for k, v in upstream.headers.items():
                        if k.lower() not in _HOP_HEADERS | {"content-encoding"}:
                            self.send_header(k, v)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    for chunk in upstream.iter_bytes():
                        self.wfile.write(chunk)
                        self.wfile.flush()
            except ConnectionError:
                pass  # client went away mid-stream (BrokenPipe, reset, abort)
            except Exception as exc:
                if sent_headers:
                    return  # never splice an error payload into a stream already underway
                try:
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(
                        json.dumps({"error": {"type": "burnless_proxy_upstream", "message": str(exc)}}).encode()
                    )
                except Exception:
                    pass

        def _send_json(self, status: int, payload: dict) -> None:
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            except Exception:
                pass

        def _spine(self) -> None:
            """Loopback-only recovery: GET /spine/<hash> → capsule + verbatim.

            The exchanges on disk are the raw conversation; only the local
            operator (127.0.0.1/::1) may read them back through the wire.
            """
            host = (self.client_address or ("",))[0]
            if host not in ("127.0.0.1", "::1"):
                self._send_json(403, {"error": {"type": "burnless_spine_forbidden", "message": "loopback only"}})
                return
            h = self.path[len("/spine/"):].split("?", 1)[0].strip("/")
            record = resolve_ref(proxy.root, h)
            if record is None:
                self._send_json(404, {"error": {"type": "burnless_spine_not_found", "message": h}})
                return
            self._send_json(200, record)

        def _read_body(self) -> bytes | None:
            """Read the request body whichever way the client framed it —
            dropping a chunked body would forward an empty POST upstream."""
            if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                chunks = []
                while True:
                    line = self.rfile.readline()
                    try:
                        size = int(line.split(b";", 1)[0].strip(), 16)
                    except ValueError:
                        return None
                    if size == 0:
                        self.rfile.readline()  # trailing CRLF; trailers ignored
                        break
                    chunks.append(self.rfile.read(size))
                    self.rfile.readline()
                return b"".join(chunks)
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                length = 0
            return self.rfile.read(length) if length > 0 else None

        def _handle(self) -> None:
            if self.path.startswith("/spine/"):
                self._spine()
                return
            body = self._read_body()
            if self.command == "POST" and body:
                rewritten, stats = proxy.rewrite(self.path, body)
                if stats.get("applied") or stats.get("reason") not in ("not_messages",):
                    proxy.record({"path": self.path, **stats})
                body = rewritten
            self._forward(body)

        def do_POST(self):
            self._handle()

        def do_GET(self):
            self._handle()

        def do_PUT(self):
            self._handle()

        def do_DELETE(self):
            self._handle()

    return Handler


def serve(
    port: int,
    root: Path,
    *,
    upstream: str = "https://api.anthropic.com",
    keep_tail_exchanges: int = 1,
    min_exchanges: int = 3,
    codec: str = "extractive",
    max_capsule_chars: int = 700,
    cache_breakpoint: bool = True,
    raw_retention: str = "plain",
) -> None:
    proxy = SpineProxy(
        root,
        upstream=upstream,
        keep_tail_exchanges=keep_tail_exchanges,
        min_exchanges=min_exchanges,
        codec=codec,
        max_capsule_chars=max_capsule_chars,
        cache_breakpoint=cache_breakpoint,
        raw_retention=raw_retention,
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(proxy))
    print(f"burnless spine proxy on http://127.0.0.1:{port} → {proxy.upstream}")
    print(f"state: {proxy.root}  (exchanges/, capsules/, ledger.jsonl)")
    print(f'point your client at it:  export ANTHROPIC_BASE_URL="http://127.0.0.1:{port}"')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nburnless spine proxy: stopped")
