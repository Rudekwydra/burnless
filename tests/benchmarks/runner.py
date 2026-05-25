"""Benchmark runner — same scenario in baseline (Sonnet direct) vs pipeline (3-layer).

Captures per-call: tokens, cache, duration, cost (USD estimate).
Writes results to tests/benchmarks/results/<timestamp>/<id>_<mode>.json
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCENARIOS_DIR = REPO_ROOT / "tests" / "benchmarks" / "scenarios"
RESULTS_DIR = REPO_ROOT / "tests" / "benchmarks" / "results"

PRICING = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00, "cache_write_1h": 1.60, "cache_read": 0.08},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_write_1h": 6.00, "cache_read": 0.30},
    "claude-opus-4-7": {"input": 15.00, "output": 75.00, "cache_write_1h": 30.00, "cache_read": 1.50},
}


def usd_for(model: str, input_tok: int, output_tok: int, cache_w: int, cache_r: int) -> float:
    """Anthropic pricing: input_tokens, cache_creation, cache_read are SEPARATE buckets.
    input_tokens = non-cached input only (Anthropic already subtracts cache_read/write)."""
    p = PRICING.get(model, PRICING["claude-sonnet-4-6"])
    return (
        input_tok * p["input"] / 1_000_000
        + output_tok * p["output"] / 1_000_000
        + cache_w * p["cache_write_1h"] / 1_000_000
        + cache_r * p["cache_read"] / 1_000_000
    )


def parse_stream_json(stdout: str) -> dict:
    metrics = {
        "model": None,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "duration_ms": 0,
        "session_id": None,
        "final_text": "",
    }
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        t = obj.get("type")
        if t == "system" and not metrics["session_id"]:
            metrics["session_id"] = obj.get("session_id")
            metrics["model"] = obj.get("model")
        elif t == "result":
            usage = obj.get("usage") or {}
            metrics["input_tokens"] += int(usage.get("input_tokens", 0))
            metrics["output_tokens"] += int(usage.get("output_tokens", 0))
            metrics["cache_creation_input_tokens"] += int(usage.get("cache_creation_input_tokens", 0))
            metrics["cache_read_input_tokens"] += int(usage.get("cache_read_input_tokens", 0))
            metrics["duration_ms"] = int(obj.get("duration_ms", 0))
            metrics["final_text"] = obj.get("result", "") or metrics["final_text"]
            if not metrics["model"]:
                metrics["model"] = (obj.get("message") or {}).get("model") or "claude-sonnet-4-6"
    return metrics


def collect_worker_usage(burnless_root: Path, since_ts: float) -> dict:
    """Scan .burnless/logs/d*.log for files modified since `since_ts` (epoch
    seconds), parse each as stream-JSON, accumulate usage. Returns dict with:
      worker_count, total_input_tokens, total_output_tokens,
      total_cache_creation_input_tokens, total_cache_read_input_tokens,
      total_usd, per_worker (list of {did, model, ...metrics}).
    """
    log_dir = burnless_root / "logs"
    out = {
        "worker_count": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cache_creation_input_tokens": 0,
        "total_cache_read_input_tokens": 0,
        "total_usd": 0.0,
        "per_worker": [],
    }
    if not log_dir.is_dir():
        return out
    for log_path in sorted(log_dir.glob("d*.log")):
        try:
            mtime = log_path.stat().st_mtime
        except OSError:
            continue
        if mtime < since_ts:
            continue
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = parse_stream_json(text)
        if m["input_tokens"] == 0 and m["output_tokens"] == 0:
            continue
        model = m["model"] or "claude-haiku-4-5-20251001"
        worker_usd = usd_for(
            model,
            m["input_tokens"], m["output_tokens"],
            m["cache_creation_input_tokens"], m["cache_read_input_tokens"],
        )
        out["worker_count"] += 1
        out["total_input_tokens"] += m["input_tokens"]
        out["total_output_tokens"] += m["output_tokens"]
        out["total_cache_creation_input_tokens"] += m["cache_creation_input_tokens"]
        out["total_cache_read_input_tokens"] += m["cache_read_input_tokens"]
        out["total_usd"] += worker_usd
        out["per_worker"].append({
            "did": log_path.stem,
            "model": model,
            "input_tokens": m["input_tokens"],
            "output_tokens": m["output_tokens"],
            "cache_creation_input_tokens": m["cache_creation_input_tokens"],
            "cache_read_input_tokens": m["cache_read_input_tokens"],
            "usd": worker_usd,
        })
    return out


def call_baseline(prompt: str, model: str, session_id: str | None, cwd: Path, cold_cache: bool = False) -> dict:
    """Single Sonnet/Opus call, no pipeline.
    cold_cache=True → drop session resume to force cold cache (fair comparison vs pipeline encoder)."""
    if cold_cache:
        session_id = None
    cmd = [
        "/opt/homebrew/bin/claude", "-p",
        "--model", model,
        "--permission-mode", "bypassPermissions",
        "--allowedTools", "Read,Edit,Write,Bash,Glob,Grep,LS",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]
    if session_id:
        cmd += ["--resume", session_id]
    t0 = time.time()
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=str(cwd), timeout=600)
    wall_s = time.time() - t0
    metrics = parse_stream_json(proc.stdout)
    metrics["wall_s"] = wall_s
    metrics["exit_code"] = proc.returncode
    metrics["layer"] = "baseline"
    metrics["usd"] = usd_for(
        metrics["model"] or model,
        metrics["input_tokens"], metrics["output_tokens"],
        metrics["cache_creation_input_tokens"], metrics["cache_read_input_tokens"],
    )
    return metrics


def call_light(prompt: str, session_state: dict, cwd: Path, maestro_model: str = "claude-sonnet-4-6") -> dict:
    """Light mode: Maestro direct (no encoder/decoder Haiku layer).
    User prompt → Maestro (with HARD RULES, forced worker delegation) → response direct.
    Isolates the gain from tier delegation alone (no encoder/decoder overhead)."""
    sys.path.insert(0, str(cwd / "src"))
    from burnless.maestro_layer import process_envelope

    t = time.time()
    workers_since = t
    mae_result = process_envelope(prompt, cwd, compression_mode="tight", model=maestro_model, timeout=300)
    wall = time.time() - t
    response_envelope = mae_result.get("response_envelope", {})
    response_text = json.dumps(response_envelope) if isinstance(response_envelope, dict) else str(response_envelope)
    mae_usage = mae_result.get("usage", {}) or {}
    worker_usage = collect_worker_usage(cwd / ".burnless", workers_since)
    mae_metrics = {
        "layer": "maestro_light",
        "model": mae_usage.get("model") or "claude-sonnet-4-6",
        "input_tokens": mae_usage.get("input_tokens", 0),
        "output_tokens": mae_usage.get("output_tokens", 0),
        "cache_creation_input_tokens": mae_usage.get("cache_creation_input_tokens", 0),
        "cache_read_input_tokens": mae_usage.get("cache_read_input_tokens", 0),
        "duration_ms": mae_usage.get("duration_ms") or int(wall * 1000),
        "wall_s": wall,
        "exit_code": mae_result.get("maestro_exit_code", 0),
        "session_id": mae_result.get("maestro_session_id"),
        "final_text": response_text,
    }
    mae_metrics["usd"] = usd_for(
        mae_metrics["model"],
        mae_metrics["input_tokens"], mae_metrics["output_tokens"],
        mae_metrics["cache_creation_input_tokens"], mae_metrics["cache_read_input_tokens"],
    )
    session_state["maestro_session_id"] = mae_metrics["session_id"]
    return {
        "maestro": mae_metrics,
        "workers": worker_usage,
        "total_usd": mae_metrics["usd"] + worker_usage["total_usd"],
        "total_input_tokens": mae_metrics["input_tokens"] + worker_usage["total_input_tokens"],
        "total_output_tokens": mae_metrics["output_tokens"] + worker_usage["total_output_tokens"],
        "final_text": mae_metrics["final_text"],
        "wall_s_total": mae_metrics["wall_s"],
    }


def call_pipeline(prompt: str, session_state: dict, cwd: Path, maestro_model: str = "claude-sonnet-4-6") -> dict:
    """Full 3-layer: encoder Haiku → maestro (Sonnet or Opus) → decoder Haiku.
    session_state holds maestro_session_id across turns.
    """
    sys.path.insert(0, str(cwd / "src"))
    from burnless.maestro_layer import process_envelope

    # ENCODER (Haiku one-shot)
    encoder_prompt = (
        "You are the Burnless Telegraph Encoder. "
        "Compress the user message below into a compact envelope JSON with keys: "
        "intent (1 sentence imperative), key_entities (array of literals), "
        "markers (URGENCY|FRUSTRATION|DECISION|CELEBRATION|PERSONAL|TRAUMA|META_COMMENT|LEARNING|SUNK_COST|HYPE_CHECK if applicable), "
        "literal_quotes (critical phrases to preserve verbatim, may be empty). "
        "Output ONLY the JSON object. No prose.\n\n"
        f"[USER MESSAGE]\n{prompt}"
    )
    cmd_enc = [
        "/opt/homebrew/bin/claude", "-p",
        "--model", "claude-haiku-4-5-20251001",
        "--permission-mode", "bypassPermissions",
        "--allowedTools", "",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]
    t0 = time.time()
    enc_proc = subprocess.run(cmd_enc, input=encoder_prompt, capture_output=True, text=True, cwd=str(cwd), timeout=60)
    enc_metrics = parse_stream_json(enc_proc.stdout)
    enc_metrics["wall_s"] = time.time() - t0
    enc_metrics["exit_code"] = enc_proc.returncode
    enc_metrics["layer"] = "encoder"
    enc_metrics["usd"] = usd_for(enc_metrics["model"] or "claude-haiku-4-5-20251001",
                                  enc_metrics["input_tokens"], enc_metrics["output_tokens"],
                                  enc_metrics["cache_creation_input_tokens"], enc_metrics["cache_read_input_tokens"])

    envelope = enc_metrics["final_text"]

    # MAESTRO (Sonnet/Opus, session resumed via maestro_layer) — real usage now
    t1 = time.time()
    workers_since = t1
    mae_result = process_envelope(envelope, cwd, compression_mode="tight", model=maestro_model, timeout=300)
    mae_wall = time.time() - t1
    response_envelope = mae_result.get("response_envelope", {})
    response_text = json.dumps(response_envelope) if isinstance(response_envelope, dict) else str(response_envelope)
    mae_usage = mae_result.get("usage", {}) or {}
    worker_usage = collect_worker_usage(cwd / ".burnless", workers_since)
    mae_metrics = {
        "layer": "maestro",
        "model": mae_usage.get("model") or "claude-sonnet-4-6",
        "input_tokens": mae_usage.get("input_tokens", 0),
        "output_tokens": mae_usage.get("output_tokens", 0),
        "cache_creation_input_tokens": mae_usage.get("cache_creation_input_tokens", 0),
        "cache_read_input_tokens": mae_usage.get("cache_read_input_tokens", 0),
        "duration_ms": mae_usage.get("duration_ms") or int(mae_wall * 1000),
        "wall_s": mae_wall,
        "exit_code": mae_result.get("maestro_exit_code", 0),
        "session_id": mae_result.get("maestro_session_id"),
        "final_text": response_text,
    }
    mae_metrics["usd"] = usd_for(
        mae_metrics["model"],
        mae_metrics["input_tokens"], mae_metrics["output_tokens"],
        mae_metrics["cache_creation_input_tokens"], mae_metrics["cache_read_input_tokens"],
    )

    # DECODER (Haiku one-shot)
    decoder_prompt = (
        "You are the Burnless Telegraph Decoder. Translate the structured response into "
        "EXACTLY what the user asked for, in the user's language. "
        "Rules:\n"
        "- Match the user's question framing. If they asked 'how many X?', answer with just the number.\n"
        "- Never expose internal IDs (d281, capsule names, delegations_made).\n"
        "- Never add 'success', 'OK', metadata, or commentary.\n"
        "- Preserve key entities (numbers, paths, names) literally.\n"
        "- Be as terse as the question demands. Trivial question → trivial answer.\n\n"
        f"[USER ORIGINAL QUESTION]\n{prompt}\n\n"
        f"[STRUCTURED RESPONSE TO TRANSLATE]\n{response_text}\n\n"
        f"[ADDITIONAL HINT]\n{mae_result.get('decoder_hint', '')}\n\n"
        "Now answer the user's question directly. No preamble."
    )
    t2 = time.time()
    dec_proc = subprocess.run(cmd_enc, input=decoder_prompt, capture_output=True, text=True, cwd=str(cwd), timeout=60)
    dec_metrics = parse_stream_json(dec_proc.stdout)
    dec_metrics["wall_s"] = time.time() - t2
    dec_metrics["exit_code"] = dec_proc.returncode
    dec_metrics["layer"] = "decoder"
    dec_metrics["usd"] = usd_for(dec_metrics["model"] or "claude-haiku-4-5-20251001",
                                  dec_metrics["input_tokens"], dec_metrics["output_tokens"],
                                  dec_metrics["cache_creation_input_tokens"], dec_metrics["cache_read_input_tokens"])

    session_state["maestro_session_id"] = mae_metrics["session_id"]
    return {
        "encoder": enc_metrics,
        "maestro": mae_metrics,
        "decoder": dec_metrics,
        "workers": worker_usage,
        "total_usd": enc_metrics["usd"] + mae_metrics["usd"] + dec_metrics["usd"] + worker_usage["total_usd"],
        "total_input_tokens": enc_metrics["input_tokens"] + mae_metrics["input_tokens"] + dec_metrics["input_tokens"] + worker_usage["total_input_tokens"],
        "total_output_tokens": enc_metrics["output_tokens"] + mae_metrics["output_tokens"] + dec_metrics["output_tokens"] + worker_usage["total_output_tokens"],
        "final_text": dec_metrics["final_text"],
        "wall_s_total": enc_metrics["wall_s"] + mae_metrics["wall_s"] + dec_metrics["wall_s"],
    }


def run_scenario(scenario_path: Path, mode: str, ts_dir: Path,
                 baseline_model: str = "claude-sonnet-4-6",
                 maestro_model: str = "claude-sonnet-4-6",
                 pause_between_turns: float = 0.0) -> dict:
    scenario = json.loads(scenario_path.read_text())
    cwd = REPO_ROOT
    label_map = {"baseline": baseline_model, "light": f"light({maestro_model})", "pipeline": f"pipeline({maestro_model})"}
    results = {"scenario_id": scenario["id"], "mode": mode, "model": label_map.get(mode, mode),
               "baseline_model": baseline_model, "maestro_model": maestro_model, "turns": []}
    session_id = None
    pipeline_session = {"maestro_session_id": None}
    tmp_dir = os.environ.get("BENCHMARK_TMP_DIR", f"/tmp/burnless_bench_{mode}_{int(time.time())}")
    prev_turn_end_ts = None
    for i, turn in enumerate(scenario["turns"]):
        user_msg = turn["user"].replace("{TMP_DIR}", tmp_dir)
        print(f"  turn {i+1}/{len(scenario['turns'])}: {user_msg[:60]}...", file=sys.stderr)
        if mode == "baseline":
            m = call_baseline(user_msg, baseline_model, session_id, cwd)
            session_id = m["session_id"] or session_id
            results["turns"].append({"turn": i+1, "user_msg": user_msg, "metrics": m})
        elif mode == "light":
            m = call_light(user_msg, pipeline_session, cwd, maestro_model=maestro_model)
            results["turns"].append({"turn": i+1, "user_msg": user_msg, "metrics": m})
        else:
            m = call_pipeline(user_msg, pipeline_session, cwd, maestro_model=maestro_model)
            results["turns"].append({"turn": i+1, "user_msg": user_msg, "metrics": m})

        # Compute elapsed since previous turn end (0 for turn 1)
        now = time.time()
        dt_since_prev = (now - prev_turn_end_ts) if prev_turn_end_ts is not None else 0.0

        # Pull cache stats (handles baseline / light / pipeline shapes)
        brain_read  = (m.get('cache_read_input_tokens')
                       or (m.get('maestro') or {}).get('cache_read_input_tokens') or 0)
        brain_write = (m.get('cache_creation_input_tokens')
                       or (m.get('maestro') or {}).get('cache_creation_input_tokens') or 0)
        w = m.get('workers') or {}
        wcount = int(w.get('worker_count') or 0)
        wread  = int(w.get('total_cache_read_input_tokens') or 0)
        wwrite = int(w.get('total_cache_creation_input_tokens') or 0)
        w_hits = sum(1 for pw in (w.get('per_worker') or [])
                     if int(pw.get('cache_read_input_tokens') or 0) > 1000)
        usd  = float(m.get('usd') or m.get('total_usd') or 0.0)
        wall = float(m.get('wall_s') or m.get('wall_s_total') or 0.0)

        warn = ' WARN >5min' if dt_since_prev > 300 else ''
        print(
            f'  turn {i+1}: brain[r={brain_read/1e3:.1f}k w={brain_write/1e3:.1f}k] '
            f'workers[n={wcount} r={wread/1e3:.1f}k w={wwrite/1e3:.1f}k '
            f'warm_hit={w_hits}/{wcount}] '
            f'usd={usd:.4f} wall={wall:.1f}s elapsed_prev={dt_since_prev:.1f}s{warn}',
            file=sys.stderr,
        )

        # Tag the turn dict with elapsed_since_prev_s
        results['turns'][-1]['elapsed_since_prev_s'] = dt_since_prev

        prev_turn_end_ts = time.time()

        # Pause if requested and not last turn
        if pause_between_turns > 0 and (i + 1) < len(scenario['turns']):
            print(f'  pausing {pause_between_turns:.0f}s...', file=sys.stderr)
            time.sleep(pause_between_turns)

    # totals
    if mode == "baseline":
        results["totals"] = {
            "input_tokens": sum(t["metrics"]["input_tokens"] for t in results["turns"]),
            "output_tokens": sum(t["metrics"]["output_tokens"] for t in results["turns"]),
            "cache_read": sum(t["metrics"]["cache_read_input_tokens"] for t in results["turns"]),
            "cache_write": sum(t["metrics"]["cache_creation_input_tokens"] for t in results["turns"]),
            "usd": sum(t["metrics"]["usd"] for t in results["turns"]),
            "wall_s": sum(t["metrics"]["wall_s"] for t in results["turns"]),
        }
    else:
        results["totals"] = {
            "input_tokens": sum(t["metrics"]["total_input_tokens"] for t in results["turns"]),
            "output_tokens": sum(t["metrics"]["total_output_tokens"] for t in results["turns"]),
            "usd": sum(t["metrics"]["total_usd"] for t in results["turns"]),
            "wall_s": sum(t["metrics"]["wall_s_total"] for t in results["turns"]),
        }
    out_file = ts_dir / f"{scenario['id']}_{mode}.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"  → {out_file.relative_to(REPO_ROOT)}  USD={results['totals']['usd']:.4f}  wall={results['totals']['wall_s']:.1f}s", file=sys.stderr)
    return results


def main():
    p = argparse.ArgumentParser()
    p.add_argument("scenario", help="scenario id (T1_trivial, T2_medium, T6_arch_escalation, ...)")
    p.add_argument("--mode", choices=["baseline", "light", "pipeline", "all"], default="all")
    p.add_argument("--baseline-model", default="claude-sonnet-4-6")
    p.add_argument("--maestro-model", default="claude-sonnet-4-6")
    p.add_argument("--label", default=None, help="custom subdir suffix for results")
    p.add_argument('--pause-between-turns', type=float, default=0.0,
                   help='seconds to sleep between turns (for cache TTL tests)')
    args = p.parse_args()

    scenario_path = SCENARIOS_DIR / f"{args.scenario}.json"
    if not scenario_path.exists():
        print(f"scenario not found: {scenario_path}", file=sys.stderr)
        sys.exit(2)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"_{args.label}" if args.label else ""
    ts_dir = RESULTS_DIR / f"{ts}{suffix}"
    ts_dir.mkdir(parents=True, exist_ok=True)

    for mode in (["baseline", "light", "pipeline"] if args.mode == "all" else [args.mode]):
        print(f"=== {args.scenario} / {mode} / baseline={args.baseline_model} maestro={args.maestro_model} ===", file=sys.stderr)
        run_scenario(scenario_path, mode, ts_dir, args.baseline_model, args.maestro_model, args.pause_between_turns)


if __name__ == "__main__":
    main()
