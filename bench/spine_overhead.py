#!/usr/bin/env python3
"""Spine proxy overhead — how much latency the porteiro itself costs.

Two measurements, both offline (no provider, no keys):

1. transform: SpineProxy.rewrite() wall time per request, across history
   sizes, capsules pre-frozen (the steady state of a long session).
2. hop: end-to-end HTTP request latency through the proxy against an
   instant fake upstream, minus the same request straight to the upstream —
   the delta is what the proxy adds to every call.

Reference point: headroom's published production overhead is P50 52ms.
The spine's transform is pure dict surgery on the live path (compression
happens off-path), so the budget here is single-digit milliseconds.

Deterministic; run with --iters to change sample count.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from burnless.proxy import spine  # noqa: E402
from burnless.proxy.compressor import compress_exchange  # noqa: E402
from burnless.proxy.server import SpineProxy, _make_handler  # noqa: E402


def build_history(n_exchanges: int) -> list:
    msgs: list = []
    for i in range(n_exchanges):
        msgs.append({"role": "user", "content": f"question {i}: " + ("detail and context " * 60)})
        msgs.append({"role": "assistant", "content": f"answer {i}: " + ("reasoning and result " * 80)})
    msgs.append({"role": "user", "content": "current prompt: what next?"})
    return msgs


def freeze_all(proxy: SpineProxy, msgs: list) -> None:
    completed, _ = spine.split_exchanges(msgs)
    for ex in completed:
        h = spine.exchange_hash(ex)
        proxy.store.put_original(h, ex)
        cap = compress_exchange(ex, h)
        if cap:
            proxy.store.put_capsule(h, cap)


def pct(samples: list[float], p: float) -> float:
    return statistics.quantiles(samples, n=100)[int(p) - 1]


def bench_transform(iters: int) -> None:
    print(f"transform latency (rewrite(), capsules frozen, {iters} iters):")
    print(f"{'exchanges':>10} {'body_kb':>8} {'p50_ms':>8} {'p90_ms':>8} {'p99_ms':>8} {'applied':>8}")
    with TemporaryDirectory() as td:
        for n in (5, 20, 50, 100):
            proxy = SpineProxy(Path(td) / f"s{n}", upstream="http://127.0.0.1:1")
            msgs = build_history(n)
            freeze_all(proxy, msgs)
            body = json.dumps({"model": "m", "max_tokens": 100, "messages": msgs}).encode()
            samples = []
            applied = False
            for _ in range(iters):
                t0 = time.perf_counter()
                _, stats = proxy.rewrite("/v1/messages", body)
                samples.append((time.perf_counter() - t0) * 1000)
                applied = bool(stats.get("applied"))
            print(
                f"{n:>10} {len(body) // 1024:>8} {pct(samples, 50):>8.2f} "
                f"{pct(samples, 90):>8.2f} {pct(samples, 99):>8.2f} {str(applied):>8}"
            )


class _InstantUpstream(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        resp = b'{"id":"msg","content":[{"type":"text","text":"ok"}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)


def bench_hop(iters: int) -> None:
    up = ThreadingHTTPServer(("127.0.0.1", 0), _InstantUpstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    up_port = up.server_address[1]
    with TemporaryDirectory() as td:
        proxy = SpineProxy(Path(td) / "hop", upstream=f"http://127.0.0.1:{up_port}")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(proxy))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        px_port = srv.server_address[1]

        msgs = build_history(20)
        freeze_all(proxy, msgs)
        body = json.dumps({"model": "m", "max_tokens": 100, "messages": msgs}).encode()

        def roundtrip(port: int) -> float:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/messages",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            t0 = time.perf_counter()
            with urllib.request.urlopen(req) as r:
                r.read()
            return (time.perf_counter() - t0) * 1000

        direct = [roundtrip(up_port) for _ in range(iters)]
        through = [roundtrip(px_port) for _ in range(iters)]
        d50, t50 = statistics.median(direct), statistics.median(through)
        print(f"\nhop latency (20-exchange body, {iters} iters):")
        print(f"  direct→upstream   p50 {d50:6.2f} ms   p90 {pct(direct, 90):6.2f} ms")
        print(f"  through proxy     p50 {t50:6.2f} ms   p90 {pct(through, 90):6.2f} ms")
        print(f"  proxy overhead    p50 {t50 - d50:6.2f} ms")
        srv.shutdown()
    up.shutdown()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=200)
    args = ap.parse_args()
    bench_transform(args.iters)
    bench_hop(args.iters)
