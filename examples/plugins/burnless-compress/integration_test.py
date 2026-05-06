"""End-to-end integration test for burnless-compress plugin.

Starts the plugin server on a free port, fires hook payloads via curl, asserts
JSON schema, measures real-token compression with tiktoken (if available), and
saves artifacts to ~/.burnless/test_data/plugin_integration/{ts}/.

Run:
    ollama serve   # if not already running
    ollama pull qwen2.5:7b-instruct   # one-time
    python examples/plugins/burnless-compress/integration_test.py

Exits 0 on pass, 1 on any failure. Zero Anthropic API consumption — all calls
are Ollama-local.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server.py"

CASES = [
    {
        "label": "verbose request with pleasantries",
        "hook": "pre_worker_prompt",
        "prompt": "olha por favor implementa o teste de cache no claude -p mas com haiku primeiro pra economizar quota, valeu obrigado!",
    },
    {
        "label": "bug report long",
        "hook": "pre_worker_prompt",
        "prompt": "encontrei um bug na função de upload — quando o arquivo passa de 50MB o nginx cortou a conexão antes de chegar no app, mas não tem mensagem de erro pro usuário, só fica girando",
    },
    {
        "label": "short already-tight",
        "hook": "pre_worker_prompt",
        "prompt": "fix bug in auth.py login flow",
    },
    {
        "label": "user_capsule via pre_brain_prompt",
        "hook": "pre_brain_prompt",
        "user_capsule": "preciso urgente que voce escreva um script python que leia um csv chamado dados.csv com colunas nome idade salario e calcule a media de salario por faixa etaria",
    },
    {
        "label": "empty prompt (passthrough)",
        "hook": "pre_worker_prompt",
        "prompt": "",
    },
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def approx_tokens(text: str) -> int:
    try:
        import tiktoken
        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except ImportError:
        return max(1, len(text) // 4)


def post(url: str, body: dict, timeout: int = 60) -> dict:
    payload = json.dumps(body)
    proc = subprocess.run(
        ["curl", "-sS", "--max-time", str(timeout), url,
         "-H", "Content-Type: application/json", "-d", payload],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed: {proc.stderr[:300]}")
    return json.loads(proc.stdout)


def wait_for_server(url: str, deadline_s: int = 15) -> bool:
    start = time.time()
    while time.time() - start < deadline_s:
        try:
            post(url, {"hook": "ping"}, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> int:
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["BURNLESS_COMPRESS_PORT"] = str(port)
    env["BURNLESS_COMPRESS_LANG"] = "pt"

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path.home() / ".burnless" / "test_data" / "plugin_integration" / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"starting server on :{port} (lang=pt, model={env.get('BURNLESS_COMPRESS_MODEL', 'qwen2.5:7b-instruct')})")
    server_log = out_dir / "server.log"
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        env=env,
        stdout=open(server_log, "w"),
        stderr=subprocess.STDOUT,
    )
    try:
        if not wait_for_server(url):
            print("FAIL: server did not become reachable in 15s", file=sys.stderr)
            print(f"  see log: {server_log}")
            return 1
        print("server up\n")

        results = []
        passed = 0
        for i, case in enumerate(CASES, 1):
            payload = {"hook": case["hook"]}
            if "prompt" in case:
                payload["prompt"] = case["prompt"]
                payload["system_prompt"] = ""
            if "user_capsule" in case:
                payload["user_capsule"] = case["user_capsule"]
                payload["system_blocks"] = []
            try:
                resp = post(url, payload, timeout=60)
            except Exception as exc:
                print(f"[{i}] {case['label']}: NETWORK FAIL — {exc}", file=sys.stderr)
                results.append({"label": case["label"], "status": "NETWORK_FAIL", "error": str(exc)})
                continue

            # Schema check: hook-specific output keys
            if case["hook"] == "pre_worker_prompt":
                if "prompt" not in resp:
                    print(f"[{i}] {case['label']}: SCHEMA FAIL — missing 'prompt' in response: {resp}", file=sys.stderr)
                    results.append({"label": case["label"], "status": "SCHEMA_FAIL", "response": resp})
                    continue
                in_text = case["prompt"]
                out_text = resp["prompt"]
            else:  # pre_brain_prompt
                if "user_capsule" not in resp:
                    print(f"[{i}] {case['label']}: SCHEMA FAIL — missing 'user_capsule': {resp}", file=sys.stderr)
                    results.append({"label": case["label"], "status": "SCHEMA_FAIL", "response": resp})
                    continue
                in_text = case["user_capsule"]
                out_text = resp["user_capsule"]

            t_in = approx_tokens(in_text)
            t_out = approx_tokens(out_text)
            ratio = (t_in / t_out) if t_out > 0 else float("inf") if t_in > 0 else 1.0

            # Empty input must passthrough — out should equal in (both empty)
            if not in_text:
                ok = (out_text == in_text)
            else:
                # Non-empty input must produce non-empty output (fail-open guarantees this)
                ok = bool(out_text)

            status = "PASS" if ok else "FAIL"
            if ok:
                passed += 1
            print(f"[{i}/{len(CASES)}] {status}  {case['label']}")
            print(f"     hook={case['hook']}  in={t_in}t  out={t_out}t  ratio={ratio:.2f}x")
            print(f"     IN:  {in_text[:100]}{'...' if len(in_text) > 100 else ''}")
            print(f"     OUT: {out_text[:100]}{'...' if len(out_text) > 100 else ''}")
            print()

            results.append({
                "label": case["label"], "hook": case["hook"], "status": status,
                "in_text": in_text, "out_text": out_text,
                "in_tokens": t_in, "out_tokens": t_out, "ratio": ratio,
            })

        # Summary
        non_empty = [r for r in results if r.get("in_tokens", 0) > 0 and r.get("status") == "PASS"]
        if non_empty:
            avg_ratio = sum(r["ratio"] for r in non_empty) / len(non_empty)
            print(f"summary: {passed}/{len(CASES)} passed, avg ratio (non-empty inputs) = {avg_ratio:.2f}x")
        else:
            print(f"summary: {passed}/{len(CASES)} passed")

        artifact = out_dir / "results.json"
        artifact.write_text(json.dumps({
            "session": ts,
            "port": port,
            "model": env.get("BURNLESS_COMPRESS_MODEL", "qwen2.5:7b-instruct"),
            "results": results,
        }, indent=2))
        print(f"saved: {artifact}")
        print(f"server log: {server_log}")

        return 0 if passed == len(CASES) else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
