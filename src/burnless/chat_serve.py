"""Local, read-only HTTP viewer for a stitched Burnless chat timeline."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from .chat import stitch_events, turn_to_json


HOST = "127.0.0.1"


PAGE_HTML = r"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Burnless — continuous chat</title>
  <style>
    :root {
      color-scheme: light;
      --paper: #ece8dd;
      --paper-deep: #dfd8c9;
      --ink: #1d1c19;
      --muted: #777164;
      --rule: #c9c0ae;
      --signal: #db4b2a;
      --assistant: #fffdf7;
      --user: #282722;
      --user-ink: #f5f0e5;
    }

    * { box-sizing: border-box; }

    html { min-height: 100%; background: var(--paper); }

    body {
      min-height: 100vh;
      margin: 0;
      color: var(--ink);
      background:
        linear-gradient(90deg, transparent 0 31.95%, rgba(29, 28, 25, .06) 32%, transparent 32.08%),
        repeating-linear-gradient(0deg, rgba(29, 28, 25, .018) 0 1px, transparent 1px 5px),
        var(--paper);
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
    }

    .shell {
      width: min(1180px, calc(100% - 48px));
      margin: 0 auto;
      padding: 30px 0 90px;
    }

    header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 24px;
      padding: 0 0 20px;
      border-bottom: 1px solid var(--ink);
    }

    .wordmark {
      margin: 0;
      font-size: clamp(2.3rem, 5vw, 5.2rem);
      font-weight: 400;
      letter-spacing: -.07em;
      line-height: .8;
    }

    .wordmark sup {
      position: relative;
      top: -1.6em;
      margin-left: .45rem;
      color: var(--signal);
      font: 600 .62rem/1 "SFMono-Regular", Menlo, Consolas, monospace;
      letter-spacing: .12em;
      text-transform: uppercase;
    }

    .live {
      display: flex;
      align-items: center;
      gap: 9px;
      color: var(--muted);
      font: 600 .67rem/1 "SFMono-Regular", Menlo, Consolas, monospace;
      letter-spacing: .1em;
      text-transform: uppercase;
    }

    .live-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--signal);
      box-shadow: 0 0 0 0 rgba(219, 75, 42, .5);
      animation: pulse 2s infinite;
    }

    .live.offline .live-dot { background: var(--muted); animation: none; }

    .layout {
      display: grid;
      grid-template-columns: minmax(180px, .65fr) minmax(0, 1.65fr);
      gap: clamp(36px, 7vw, 110px);
      padding-top: 34px;
    }

    aside {
      position: sticky;
      top: 28px;
      align-self: start;
      min-height: 280px;
    }

    .eyebrow {
      margin: 0 0 18px;
      color: var(--muted);
      font: 600 .63rem/1.3 "SFMono-Regular", Menlo, Consolas, monospace;
      letter-spacing: .13em;
      text-transform: uppercase;
    }

    .context-stack { display: grid; gap: 3px; }

    .session-chip {
      position: relative;
      min-height: 54px;
      padding: 12px 14px;
      overflow: hidden;
      border-left: 2px solid var(--rule);
      background: rgba(255, 253, 247, .28);
      color: var(--muted);
      font: .68rem/1.45 "SFMono-Regular", Menlo, Consolas, monospace;
      animation: arrive .35s ease both;
    }

    .session-chip::after {
      content: "";
      position: absolute;
      inset: auto 0 0;
      height: 34%;
      background: linear-gradient(transparent, rgba(236, 232, 221, .92));
    }

    .session-chip.current {
      border-left-color: var(--signal);
      color: var(--ink);
      background: rgba(255, 253, 247, .74);
    }

    .session-chip strong {
      display: block;
      margin-bottom: 2px;
      color: inherit;
      font-weight: 600;
    }

    .rail-note {
      max-width: 20ch;
      margin: 20px 0 0;
      color: var(--muted);
      font-size: .82rem;
      font-style: italic;
      line-height: 1.45;
    }

    main { min-width: 0; }

    .timeline-heading {
      display: flex;
      justify-content: space-between;
      gap: 20px;
      margin: 0 0 30px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--rule);
      color: var(--muted);
      font: .65rem/1 "SFMono-Regular", Menlo, Consolas, monospace;
      letter-spacing: .08em;
      text-transform: uppercase;
    }

    #timeline { display: grid; gap: 14px; }

    .turn {
      width: min(88%, 700px);
      padding: 18px 21px 20px;
      border: 1px solid rgba(29, 28, 25, .12);
      box-shadow: 0 8px 30px rgba(29, 28, 25, .05);
      animation: arrive .28s ease both;
    }

    .turn.assistant {
      justify-self: start;
      background: var(--assistant);
      border-radius: 2px 18px 18px 18px;
    }

    .turn.user {
      justify-self: end;
      color: var(--user-ink);
      background: var(--user);
      border-color: var(--user);
      border-radius: 18px 2px 18px 18px;
    }

    .turn-meta {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 11px;
      color: var(--muted);
      font: 600 .61rem/1 "SFMono-Regular", Menlo, Consolas, monospace;
      letter-spacing: .09em;
      text-transform: uppercase;
    }

    .user .turn-meta { color: #aaa393; }

    .turn-text {
      margin: 0;
      overflow-wrap: anywhere;
      white-space: pre-wrap;
      font-size: clamp(.98rem, 1.55vw, 1.08rem);
      line-height: 1.58;
    }

    .boundary {
      display: grid;
      grid-template-columns: 1fr auto 1fr;
      align-items: center;
      gap: 13px;
      margin: 26px 0 20px;
      color: var(--signal);
      font: 600 .61rem/1.3 "SFMono-Regular", Menlo, Consolas, monospace;
      letter-spacing: .08em;
      text-transform: uppercase;
    }

    .boundary::before, .boundary::after {
      content: "";
      height: 1px;
      background: linear-gradient(90deg, transparent, var(--signal));
    }

    .boundary::after { background: linear-gradient(90deg, var(--signal), transparent); }

    .missing {
      padding: 13px 16px;
      border: 1px dashed var(--muted);
      color: var(--muted);
      font: .7rem/1.4 "SFMono-Regular", Menlo, Consolas, monospace;
    }

    .empty {
      padding: 70px 0;
      color: var(--muted);
      font-size: 1.1rem;
      font-style: italic;
    }

    @keyframes arrive {
      from { opacity: 0; transform: translateY(7px); }
      to { opacity: 1; transform: translateY(0); }
    }

    @keyframes pulse {
      70% { box-shadow: 0 0 0 8px rgba(219, 75, 42, 0); }
      100% { box-shadow: 0 0 0 0 rgba(219, 75, 42, 0); }
    }

    @media (max-width: 720px) {
      body { background: var(--paper); }
      .shell { width: min(100% - 28px, 620px); padding-top: 22px; }
      header { align-items: flex-end; }
      .layout { grid-template-columns: 1fr; gap: 28px; }
      aside { position: static; min-height: 0; }
      .context-stack { grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); }
      .session-chip { min-height: 48px; }
      .rail-note { display: none; }
      .turn { width: 94%; }
    }

    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { animation: none !important; scroll-behavior: auto !important; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <h1 class="wordmark">burnless<sup>eternal chat</sup></h1>
      <div id="live" class="live"><span class="live-dot"></span><span id="status">live</span></div>
    </header>
    <div class="layout">
      <aside aria-label="Janelas de contexto">
        <p class="eyebrow">janelas da llm</p>
        <div id="sessions" class="context-stack"></div>
        <p class="rail-note">O contexto expira. A conversa continua.</p>
      </aside>
      <main>
        <div class="timeline-heading"><span>timeline contínua</span><span id="turn-count">0 turnos</span></div>
        <div id="timeline" aria-live="polite" aria-busy="true"></div>
      </main>
    </div>
  </div>
  <script>
    (() => {
      const timeline = document.getElementById("timeline");
      const sessions = document.getElementById("sessions");
      const live = document.getElementById("live");
      const status = document.getElementById("status");
      const turnCount = document.getElementById("turn-count");
      let signature = "";

      const node = (tag, className, text) => {
        const item = document.createElement(tag);
        if (className) item.className = className;
        if (text !== undefined) item.textContent = text;
        return item;
      };

      const hhmm = (ts) => {
        if (!ts || !ts.includes("T")) return "--:--";
        return ts.split("T", 2)[1].slice(0, 5);
      };

      const shortId = (id) => (id || "unknown").slice(0, 8);

      const renderSessions = (events) => {
        const ids = [...new Set(events.map((event) => event.session_id).filter(Boolean))];
        sessions.replaceChildren();
        ids.forEach((id, index) => {
          const chip = node("div", "session-chip" + (index === ids.length - 1 ? " current" : ""));
          chip.style.animationDelay = `${index * 35}ms`;
          chip.append(node("strong", "", `janela ${String(index + 1).padStart(2, "0")}`));
          chip.append(document.createTextNode(shortId(id)));
          sessions.append(chip);
        });
      };

      const renderTimeline = (events) => {
        timeline.replaceChildren();
        if (!events.length) timeline.append(node("div", "empty", "Aguardando os primeiros turnos…"));

        events.forEach((event, index) => {
          if (event.kind === "boundary") {
            const details = ["nova janela", event.index ? `${event.index}ª da chain` : null, shortId(event.session_id), hhmm(event.ts)].filter(Boolean);
            timeline.append(node("div", "boundary", `── ${details.join(" · ")} ──`));
            return;
          }
          if (event.kind === "missing") {
            timeline.append(node("div", "missing", event.text || `transcript não encontrado: ${shortId(event.session_id)}`));
            return;
          }
          if (event.kind !== "turn") return;

          const turn = node("article", `turn ${event.role || "assistant"}`);
          turn.style.animationDelay = `${Math.min(index * 18, 180)}ms`;
          const meta = node("div", "turn-meta");
          meta.append(node("span", "", event.role || "turn"));
          meta.append(node("time", "", hhmm(event.ts)));
          turn.append(meta, node("p", "turn-text", event.text || ""));
          timeline.append(turn);
        });

        const total = events.filter((event) => event.kind === "turn").length;
        turnCount.textContent = `${total} ${total === 1 ? "turno" : "turnos"}`;
        timeline.setAttribute("aria-busy", "false");
        renderSessions(events);
      };

      const refresh = async () => {
        try {
          const response = await fetch("/timeline.json");
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const events = await response.json();
          const nextSignature = JSON.stringify(events);
          if (nextSignature !== signature) {
            const nearBottom = innerHeight + scrollY >= document.body.offsetHeight - 180;
            signature = nextSignature;
            renderTimeline(events);
            if (nearBottom) requestAnimationFrame(() => scrollTo({ top: document.body.scrollHeight, behavior: "smooth" }));
          }
          live.classList.remove("offline");
          status.textContent = "live";
        } catch (_) {
          live.classList.add("offline");
          status.textContent = "reconnecting";
        }
      };

      refresh();
      setInterval(refresh, 1500);
    })();
  </script>
</body>
</html>
"""


def _event_payload(event: dict[str, Any], chain_id: str) -> dict[str, Any]:
    """Project a stitched event into the stable browser-facing contract."""
    if event["kind"] == "turn":
        payload = json.loads(turn_to_json(event))
        payload["kind"] = "turn"
        return payload

    kind = event["kind"]
    session_id = event.get("session_id")
    payload = {
        "chain_id": chain_id,
        "session_id": session_id,
        "seq": None,
        "role": None,
        "ts": event.get("ts", ""),
        "text": (
            "nova janela"
            if kind == "boundary"
            else f"transcript não encontrado: {(session_id or '')[:8]}"
        ),
        "kind": kind,
    }
    if kind == "boundary":
        payload["index"] = event.get("index")
        payload["context_note"] = event.get("context_note")
    return payload


def timeline_payload(project_root: Path, chain_id: str) -> list[dict[str, Any]]:
    """Read and project the current stitched timeline; never cache or write it."""
    return [
        _event_payload(event, chain_id)
        for event in stitch_events(project_root, chain_id)
    ]


def _handler_for(project_root: Path, chain_id: str) -> type[BaseHTTPRequestHandler]:
    class ChatHandler(BaseHTTPRequestHandler):
        server_version = "BurnlessChat/1"

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, "text/html; charset=utf-8", PAGE_HTML.encode("utf-8"))
                return
            if path == "/timeline.json":
                body = json.dumps(
                    timeline_payload(project_root, chain_id),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", body)
                return
            self._send(404, "text/plain; charset=utf-8", b"not found\n")

        def log_message(self, _format: str, *args: Any) -> None:
            return

    return ChatHandler


def create_chat_server(project_root: Path, chain_id: str, port: int) -> HTTPServer:
    """Create a loopback-only server (separate for ephemeral-port tests)."""
    handler = _handler_for(Path(project_root), chain_id)
    return HTTPServer((HOST, int(port)), handler)


def serve_chat(project_root: Path, chain_id: str, port: int) -> int:
    """Serve the selected chain until Ctrl-C, closing the socket cleanly."""
    server = create_chat_server(project_root, chain_id, port)
    actual_port = server.server_address[1]
    print(f"http://{HOST}:{actual_port}/  chain={chain_id}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
