"""
maestro_proto - Burnless Maestro Chat (prototipo).

Brain = Haiku 4.5 default, Anthropic com cache 1h.
Workers = subprocess Haiku/Sonnet (Anthropic only no MVP).
Tools = bash (com filter), read, write, delegate (tier).
Memory = MEMORY.md no cwd ou .burnless/.
Session log = .burnless/maestro_proto/sessions/<ts>.jsonl.

Run: python -m burnless.maestro_proto
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import anthropic
    from anthropic import Anthropic
except ImportError:
    print("ERROR: anthropic SDK nao instalado. pip install anthropic", file=sys.stderr)
    sys.exit(1)


BRAIN_MODEL = "claude-haiku-4-5-20251001"
WORKER_BRONZE = "claude-haiku-4-5-20251001"
WORKER_SILVER = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4096
CACHE_TTL = "1h"
MAESTRO_PROTO_VERSION = "0.1.0"

MAX_READ_BYTES = 200_000
MAX_BASH_STDOUT = 50_000
MAX_BASH_STDERR = 10_000

FILTER_HOOK = Path.home() / ".claude" / "scripts" / "delegation_filter.sh"


def load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    for p in [
        Path.home() / ".config" / "burnless" / "anthropic.env",
        Path.home() / "antigravity" / "burnless" / ".env",
    ]:
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if key:
                        os.environ["ANTHROPIC_API_KEY"] = key
                        return key
    print("ERROR: ANTHROPIC_API_KEY nao encontrada (env nem ~/.config/burnless/anthropic.env nem burnless/.env)", file=sys.stderr)
    sys.exit(1)


def load_memory() -> str:
    candidates = [
        Path.cwd() / ".burnless" / "MEMORY.md",
        Path.cwd() / "MEMORY.md",
        Path.home() / ".burnless" / "MEMORY.md",
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            text = p.read_text(errors="replace")
            return f"\n\n## Loaded MEMORY.md from {p}\n\n{text}\n"
    return ""


def session_log_path() -> Path:
    d = Path.cwd() / ".burnless" / "maestro_proto" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"


def log_event(p: Path, event: dict) -> None:
    event["ts"] = datetime.now(timezone.utc).isoformat()
    with p.open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def sha256_hex(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def file_fingerprint(p: Path) -> dict[str, Any]:
    if not p.exists() or not p.is_file():
        return {"exists": False}
    try:
        data = p.read_bytes()
        return {
            "exists": True,
            "sha256": sha256_hex(data.decode("utf-8", errors="replace")),
            "bytes": len(data),
            "mtime_ns": p.stat().st_mtime_ns,
        }
    except Exception as e:
        return {"exists": True, "error": str(e)}


def allowed_roots() -> list[Path]:
    """
    Proto safety invariant: by default only allow read/write under:
    - current working directory
    - ~/.burnless

    Override via BURNLESS_ALLOWED_ROOTS (colon-separated absolute paths).
    """
    roots = [Path.cwd(), Path.home() / ".burnless"]
    extra = os.environ.get("BURNLESS_ALLOWED_ROOTS", "").strip()
    if extra:
        for raw in extra.split(":"):
            raw = raw.strip()
            if not raw:
                continue
            p = Path(raw).expanduser()
            if p.is_absolute():
                roots.append(p)
    # Normalize and unique while preserving order.
    out: list[Path] = []
    seen: set[str] = set()
    for r in roots:
        try:
            rr = r.resolve()
        except Exception:
            rr = r
        k = str(rr)
        if k not in seen:
            out.append(rr)
            seen.add(k)
    return out


def is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def check_path_allowed(path: Path, op: str) -> tuple[bool, str]:
    """
    op: "read" | "write"
    """
    # Disallow obviously dangerous pseudo-paths.
    s = str(path)
    if "\x00" in s:
        return False, "nul-byte"
    roots = allowed_roots()
    for r in roots:
        if is_under(path, r):
            return True, "ok"
    return False, f"outside-allowed-roots:{op}"


SYSTEM_PROMPT = """Voce e o Brain do Burnless Maestro (prototipo).

Comportamento:
- PT-BR no chat, EN em codigo/docs.
- Direto, sem hype, sem cerimonia. Roberto valoriza acao e numeros concretos.
- Implementacao pesada -> use o tool `delegate` com tier apropriado (bronze=Haiku worker, silver=Sonnet worker). Voce e leitor + decisor; mutacao fica nos workers.
- Bash mutativo passa por filter local (~/.claude/scripts/delegation_filter.sh). Read-only roda direto.
- Read e Write disponiveis pra arquivos locais.

Tier rules:
- bronze (Haiku): mecanico, spec fechada, mutacao executavel.
- silver (Sonnet): implementacao com judgment.
- (gold ficara pra proxima versao.)

Capture findings nao-obvios em MEMORY.md ou capsules Forgetless quando relevante.
"""


def build_system_blocks(memory_text: str) -> list:
    blocks = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral", "ttl": CACHE_TTL},
        }
    ]
    if memory_text:
        blocks.append({
            "type": "text",
            "text": memory_text,
            "cache_control": {"type": "ephemeral", "ttl": CACHE_TTL},
        })
    return blocks


TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command. Output captured. Mutative passes through filter.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_s": {"type": "integer", "default": 120},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read",
        "description": "Read a local file (up to ~200kb).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write",
        "description": "Write a local file (overwrites).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "delegate",
        "description": "Delegate mechanical task to a worker subprocess (bronze=Haiku, silver=Sonnet).",
        "input_schema": {
            "type": "object",
            "properties": {
                "tier": {"type": "string", "enum": ["bronze", "silver"]},
                "spec": {"type": "string"},
            },
            "required": ["tier", "spec"],
        },
    },
]


def run_filter(command: str, transcript_path: str) -> tuple[bool, str]:
    if not FILTER_HOOK.exists():
        return True, "no-filter"
    payload = json.dumps({"tool_input": {"command": command}, "transcript_path": transcript_path})
    try:
        r = subprocess.run(
            ["bash", str(FILTER_HOOK)],
            input=payload, capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            return True, "allowed"
        return False, (r.stderr or "blocked").strip()
    except Exception as e:
        return True, f"filter-error:{e}"


def tool_bash(args: dict, log: Path) -> dict:
    cmd = args.get("command", "")
    timeout_s = int(args.get("timeout_s", 120))
    allowed, reason = run_filter(cmd, transcript_path=str(log))
    if not allowed:
        log_event(log, {"event": "bash_blocked", "cmd": cmd[:300], "reason": reason[:500]})
        return {"ok": False, "error": "blocked-by-filter", "reason": reason[:500]}
    log_event(log, {"event": "bash", "cmd": cmd[:300], "filter": reason})
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout_s
        )
        return {
            "ok": True,
            "exit": r.returncode,
            "stdout": r.stdout[-MAX_BASH_STDOUT:],
            "stderr": r.stderr[-MAX_BASH_STDERR:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "timeout_s": timeout_s}


def tool_read(args: dict, log: Path) -> dict:
    p = Path(args.get("path", "")).expanduser()
    log_event(log, {"event": "read", "path": str(p)})
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": "not-found"}
    allowed, reason = check_path_allowed(p, op="read")
    if not allowed and os.environ.get("BURNLESS_UNSAFE_ALLOW_READ_ANY", "").strip() != "1":
        return {"ok": False, "error": "path-blocked", "reason": reason, "allowed_roots": [str(r) for r in allowed_roots()]}
    try:
        content = p.read_text(errors="replace")
        if len(content) > MAX_READ_BYTES:
            content = content[:MAX_READ_BYTES] + "\n[...truncated]"
        return {"ok": True, "content": content, "bytes": p.stat().st_size, "path": str(p)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_write(args: dict, log: Path) -> dict:
    p = Path(args.get("path", "")).expanduser()
    content = args.get("content", "")
    log_event(log, {"event": "write", "path": str(p), "bytes": len(content)})
    allowed, reason = check_path_allowed(p, op="write")
    if not allowed and os.environ.get("BURNLESS_UNSAFE_ALLOW_WRITE_ANY", "").strip() != "1":
        return {"ok": False, "error": "path-blocked", "reason": reason, "allowed_roots": [str(r) for r in allowed_roots()]}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return {"ok": True, "bytes_written": len(content), "path": str(p)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


_JSON_START_RE = re.compile(r"^\s*[{[]")


def _coerce_worker_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {"status": "ERR", "summary": "empty worker output", "raw_text": ""}
    if not _JSON_START_RE.match(raw):
        return {"status": "PART", "summary": "worker did not return JSON", "raw_text": raw}
    try:
        obj = json.loads(raw)
    except Exception as e:
        return {"status": "ERR", "summary": f"invalid JSON: {e}", "raw_text": raw[:50_000]}
    if isinstance(obj, dict):
        return obj
    return {"status": "PART", "summary": "worker JSON not an object", "raw_json": obj}


def tool_delegate(args: dict, client, log: Path) -> dict:
    tier = args.get("tier", "bronze")
    spec = args.get("spec", "")
    model = WORKER_BRONZE if tier == "bronze" else WORKER_SILVER
    log_event(log, {"event": "delegate", "tier": tier, "spec_len": len(spec)})
    worker_system = f"You are a {tier} worker. Execute the spec the user gives you.\n"
    try:
        r = client.messages.create(
            model=model,
            max_tokens=DEFAULT_MAX_TOKENS,
            system=worker_system,
            messages=[{"role": "user", "content": spec}],
        )
        text = "\n".join(b.text for b in r.content if hasattr(b, "text"))
        return {
            "ok": True,
            "tier": tier,
            "model": model,
            "output": text,
            "input_tokens": r.usage.input_tokens,
            "output_tokens": r.usage.output_tokens,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def dispatch_tool(name: str, args: dict, client, log: Path) -> dict:
    if name == "bash":
        return tool_bash(args, log)
    if name == "read":
        return tool_read(args, log)
    if name == "write":
        return tool_write(args, log)
    if name == "delegate":
        return tool_delegate(args, client, log)
    return {"error": f"unknown-tool:{name}"}


def render_assistant_blocks(blocks) -> str:
    parts = []
    for b in blocks:
        if hasattr(b, "text") and b.text:
            parts.append(b.text)
        elif hasattr(b, "name"):
            inp = getattr(b, "input", {})
            parts.append(f"[-> tool {b.name}({json.dumps(inp)[:200]})]")
    return "\n".join(parts)


def _ts_local() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _p(line: str, *, turn: int | None = None) -> None:
    prefix = f"[{_ts_local()}]"
    if turn is not None:
        prefix += f" t{turn:02d}"
    print(f"{prefix} {line}", flush=True)


class _Spinner:
    def __init__(self, text: str, *, enabled: bool = True):
        self._text = text
        self._enabled = enabled
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_ns: int | None = None

    def __enter__(self):
        self._start_ns = time.perf_counter_ns()
        if not self._enabled:
            return self

        def run():
            frames = ["|", "/", "-", "\\"]
            i = 0
            while not self._stop.is_set():
                msg = f"[{_ts_local()}] {frames[i % len(frames)]} {self._text}"
                sys.stderr.write("\r" + msg[:200])
                sys.stderr.flush()
                i += 1
                self._stop.wait(0.10)
            sys.stderr.write("\r" + (" " * 220) + "\r")
            sys.stderr.flush()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._enabled:
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=1.0)
        return False

    def duration_ms(self) -> int:
        if self._start_ns is None:
            return 0
        return int((time.perf_counter_ns() - self._start_ns) / 1_000_000)


@dataclass
class _BgTask:
    id: int
    tier: str
    spec: str
    created_ts: float
    status: str = "running"  # running|done|error
    result: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int | None = None


class _BgTasks:
    def __init__(self):
        self._lock = threading.Lock()
        self._next_id = 1
        self._tasks: dict[int, _BgTask] = {}

    def spawn_delegate(self, *, tier: str, spec: str, client: Any, log: Path) -> _BgTask:
        with self._lock:
            tid = self._next_id
            self._next_id += 1
            task = _BgTask(id=tid, tier=tier, spec=spec, created_ts=time.time())
            self._tasks[tid] = task

        def _run():
            t0 = time.perf_counter_ns()
            try:
                log_event(log, {"event": "bg_start", "bg_id": tid, "kind": "delegate", "tier": tier, "spec_len": len(spec)})
                out = tool_delegate({"tier": tier, "spec": spec}, client=client, log=log)
                task.result = out
                task.status = "done" if out.get("ok", True) else "error"
                if task.status == "error":
                    task.error = out.get("error") or "unknown-error"
            except Exception as e:
                task.status = "error"
                task.error = str(e)
            finally:
                task.duration_ms = int((time.perf_counter_ns() - t0) / 1_000_000)
                log_event(log, {"event": "bg_end", "bg_id": tid, "status": task.status, "duration_ms": task.duration_ms})
                if task.status == "done":
                    summary = ""
                    try:
                        summary = (task.result or {}).get("output", {}).get("summary", "")
                    except Exception:
                        summary = ""
                    _p(
                        f"[bg#{tid}] done duration_ms={task.duration_ms}"
                        + (f" summary={summary}" if summary else "")
                    )
                else:
                    _p(f"[bg#{tid}] error duration_ms={task.duration_ms} error={task.error}")

        threading.Thread(target=_run, daemon=True).start()
        return task

    def list(self) -> list[_BgTask]:
        with self._lock:
            return sorted(self._tasks.values(), key=lambda t: t.id)

    def get(self, tid: int) -> _BgTask | None:
        with self._lock:
            return self._tasks.get(tid)

    def clear_done(self) -> int:
        with self._lock:
            done = [tid for tid, t in self._tasks.items() if t.status in ("done", "error")]
            for tid in done:
                self._tasks.pop(tid, None)
            return len(done)


def chat_loop():
    key = load_api_key()
    client = Anthropic(api_key=key)
    memory = load_memory()
    system_blocks = build_system_blocks(memory)
    log = session_log_path()
    policy = {
        "version": MAESTRO_PROTO_VERSION,
        "allowed_roots": [str(r) for r in allowed_roots()],
        "read_any": os.environ.get("BURNLESS_UNSAFE_ALLOW_READ_ANY", "").strip(),
        "write_any": os.environ.get("BURNLESS_UNSAFE_ALLOW_WRITE_ANY", "").strip(),
        "filter_hook": str(FILTER_HOOK),
        "filter_fingerprint": file_fingerprint(FILTER_HOOK),
        "system_prompt_sha256": sha256_hex(SYSTEM_PROMPT),
        "tools_schema_sha256": sha256_hex(json.dumps(TOOLS, sort_keys=True)),
    }
    messages = []
    turn = 0
    bg = _BgTasks()

    print("+- Burnless Maestro (proto) ---------------------------")
    print(f"| Version: {MAESTRO_PROTO_VERSION}")
    print(f"| Brain: {BRAIN_MODEL}  (cache 1h)")
    print(f"| Workers: bronze=haiku-4.5, silver=sonnet-4.6")
    print(f"| Filter: {'ON' if FILTER_HOOK.exists() else 'off'}")
    print(f"| Memory: {'loaded' if memory else 'none'}")
    print(f"| Log:    {log}")
    print(f"| FS roots: {', '.join(policy['allowed_roots'])}")
    print(f"+- /exit, /metrics, /clear, /status, /bg --------------")
    log_event(log, {"event": "session_start", "brain": BRAIN_MODEL, "cwd": str(Path.cwd()), "policy": policy})

    total_in = total_out = total_cache_read = total_cache_write = 0
    last_usage: dict[str, int] = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}
    recent_tools: list[dict[str, Any]] = []
    state = {"phase": "awaiting_user", "worker_active": None}
    spinner_on = os.environ.get("BURNLESS_SPINNER", "1").strip() != "0"

    while True:
        try:
            user_in = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[bye]")
            break
        if not user_in:
            continue
        if user_in == "/exit":
            break
        if user_in == "/clear":
            messages = []
            print("[history cleared]")
            continue
        if user_in == "/metrics":
            print(f"in={total_in} out={total_out} cache_read={total_cache_read} cache_write={total_cache_write}")
            continue
        if user_in == "/status":
            w = state.get("worker_active") or "-"
            bg_running = sum(1 for t in bg.list() if t.status == "running")
            _p(
                "status: "
                f"turn={turn} phase={state.get('phase')} worker={w} "
                f"bg_running={bg_running} in={total_in} out={total_out} cache_read={total_cache_read} cache_write={total_cache_write}"
            )
            if recent_tools:
                last = ", ".join(
                    f"{t['tool']}:{t.get('duration_ms', 0)}ms"
                    + (f":{t['tier']}" if t.get("tier") else "")
                    for t in recent_tools[-5:]
                )
                _p(f"last_tools: {last}")
            _p(
                "last_turn: "
                f"in={last_usage['in']} out={last_usage['out']} "
                f"cache_read={last_usage['cache_read']} cache_write={last_usage['cache_write']}"
            )
            _p(f"log: {log}")
            continue
        if user_in.startswith("/bg"):
            # /bg -> list ; /bg <id> -> print result ; /bg bronze|silver <spec...> -> spawn ; /bg clear
            parts = user_in.split(" ", 2)
            if len(parts) == 1:
                tasks = bg.list()
                running = sum(1 for t in tasks if t.status == "running")
                _p(f"bg: tasks={len(tasks)} running={running}")
                for t in tasks[-10:]:
                    age_s = int(time.time() - t.created_ts)
                    _p(f"  [bg#{t.id}] {t.status} tier={t.tier} age={age_s}s")
                _p("usage: /bg bronze|silver <spec> | /bg <id> | /bg clear")
                continue
            if len(parts) >= 2 and parts[1] == "clear":
                n = bg.clear_done()
                _p(f"bg: cleared {n} done/error tasks")
                continue
            if len(parts) >= 2 and parts[1].isdigit():
                tid = int(parts[1])
                t = bg.get(tid)
                if not t:
                    _p(f"bg#{tid}: not found")
                    continue
                _p(f"[bg#{tid}] status={t.status} tier={t.tier} duration_ms={t.duration_ms}")
                if t.status == "running":
                    continue
                if t.error:
                    _p(f"[bg#{tid}] error: {t.error}")
                    continue
                if t.result:
                    print(json.dumps(t.result, ensure_ascii=False, indent=2)[:50_000], flush=True)
                continue
            if len(parts) >= 3 and parts[1] in ("bronze", "silver"):
                tier = parts[1]
                spec = parts[2].strip()
                if not spec:
                    _p("usage: /bg bronze|silver <spec>")
                    continue
                t = bg.spawn_delegate(tier=tier, spec=spec, client=client, log=log)
                _p(f"[bg#{t.id}] spawned tier={tier}")
                continue
            _p("usage: /bg | /bg bronze|silver <spec> | /bg <id> | /bg clear")
            continue

        messages.append({"role": "user", "content": user_in})
        log_event(log, {"event": "user", "text": user_in[:500]})
        turn += 1

        while True:
            state["phase"] = "waiting_api"
            try:
                _p(f"api_call: model={BRAIN_MODEL} max_tokens={DEFAULT_MAX_TOKENS}", turn=turn)
                log_event(log, {"event": "api_start", "turn": turn, "model": BRAIN_MODEL, "max_tokens": DEFAULT_MAX_TOKENS})
                with _Spinner("aguardando resposta da API…", enabled=spinner_on) as sp:
                    resp = client.messages.create(
                        model=BRAIN_MODEL,
                        max_tokens=DEFAULT_MAX_TOKENS,
                        system=system_blocks,
                        messages=messages,
                        tools=TOOLS,
                    )
                _p(f"api_ok: duration_ms={sp.duration_ms()}", turn=turn)
                log_event(log, {"event": "api_end", "turn": turn, "duration_ms": sp.duration_ms()})
            except anthropic.APIError as e:
                _p(f"api_error: {e}", turn=turn)
                log_event(log, {"event": "api_error", "turn": turn, "error": str(e)[:500]})
                break

            u = resp.usage
            total_in += u.input_tokens
            total_out += u.output_tokens
            cw = getattr(u, "cache_creation_input_tokens", 0) or 0
            cr = getattr(u, "cache_read_input_tokens", 0) or 0
            total_cache_write += cw
            total_cache_read += cr
            last_usage = {"in": u.input_tokens, "out": u.output_tokens, "cache_read": cr, "cache_write": cw}

            state["phase"] = "assistant_output"
            _p("assistant:", turn=turn)
            print(render_assistant_blocks(resp.content), flush=True)
            messages.append({"role": "assistant", "content": resp.content})
            log_event(log, {
                "event": "assistant", "stop": resp.stop_reason,
                "in": u.input_tokens, "out": u.output_tokens,
                "cache_read": cr, "cache_write": cw,
            })

            if resp.stop_reason != "tool_use":
                state["phase"] = "awaiting_user"
                break

            _p("tool_use: Brain solicitou tools; executando…", turn=turn)
            tool_results = []
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    args = dict(block.input)
                    tier = args.get("tier") if block.name == "delegate" else None
                    state["phase"] = "running_tool"
                    if block.name == "delegate":
                        state["worker_active"] = tier or "?"
                    log_event(log, {
                        "event": "tool_start",
                        "turn": turn,
                        "tool": block.name,
                        "tool_use_id": block.id,
                        "tier": tier,
                        "args_preview": json.dumps(args, ensure_ascii=False)[:300],
                    })
                    _p(
                        f"tool_start: {block.name}"
                        + (f" tier={tier}" if tier else "")
                        + f" tool_use_id={block.id}",
                        turn=turn,
                    )
                    t0 = time.perf_counter_ns()
                    result = dispatch_tool(block.name, args, client, log)
                    dur_ms = int((time.perf_counter_ns() - t0) / 1_000_000)
                    ok = bool(result.get("ok", True))
                    log_event(log, {
                        "event": "tool_end",
                        "turn": turn,
                        "tool": block.name,
                        "tool_use_id": block.id,
                        "tier": tier,
                        "ok": ok,
                        "duration_ms": dur_ms,
                    })
                    recent_tools.append({
                        "tool": block.name,
                        "tier": tier,
                        "ok": ok,
                        "duration_ms": dur_ms,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    })
                    recent_tools = recent_tools[-25:]
                    snippet = json.dumps(result, ensure_ascii=False)[:200]
                    _p(
                        f"tool_end: {block.name} ok={ok} duration_ms={dur_ms} result={snippet}"
                        + ("..." if len(json.dumps(result, ensure_ascii=False)) > 200 else ""),
                        turn=turn,
                    )
                    if block.name == "delegate":
                        state["worker_active"] = None
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })
            messages.append({"role": "user", "content": tool_results})
            state["phase"] = "waiting_api"

    log_event(log, {"event": "session_end", "turns": turn, "total_in": total_in, "total_out": total_out})
    print(f"\n[session log: {log}]")


def main():
    chat_loop()


if __name__ == "__main__":
    main()
