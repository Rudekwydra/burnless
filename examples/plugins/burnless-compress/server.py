"""burnless-compress — example plugin implementing the compression filter.

Implements two Burnless hooks (per PLUGIN_PROTOCOL.md v0.7):
  - pre_worker_prompt: compresses the user prompt before it reaches the worker subprocess
  - pre_brain_prompt: compresses the user_capsule before it reaches Brain (Anthropic SDK)

The compression is two-stage:
  Stage 1 (LLM): a local Ollama model rewrites the verbose message in compressed form
  Stage 2 (regex): telegrafista — drops articles/prepositions

Empirically (May 2026, see bench/COMPRESSION_FINDINGS.md):
  - qwen2.5:7b-instruct local + telegrafista = 2.5× compression on 50 PT samples
  - gemma3:27b-cloud + telegrafista = 1.9× compression
  - Larger models compress less; smaller models compress more aggressively

Run:
  pip install fastapi uvicorn  # or use stdlib http.server (this file uses stdlib)
  python server.py             # listens on :7711
  # then register: cp manifest.json ~/.burnless/plugins/burnless-compress.json

Requires Ollama running locally with a model pulled (qwen2.5:7b-instruct recommended).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
MODEL = os.environ.get("BURNLESS_COMPRESS_MODEL", "qwen2.5:7b-instruct")
LANG = os.environ.get("BURNLESS_COMPRESS_LANG", "pt")
PORT = int(os.environ.get("BURNLESS_COMPRESS_PORT", "7711"))

PROMPT_PT = """Comprima a mensagem do usuário para o mínimo possível. Mantenha ações, nomes, números, paths. Solte saudações, hedging, emoção. Responda APENAS com JSON: {{"compressed": "..."}}

Exemplo:
<message>oi por favor implementa o teste de cache no claude -p mas com haiku primeiro pra economizar quota, valeu!</message>
{{"compressed": "implementa teste cache claude -p, haiku primeiro"}}

<message>{message}</message>
"""

PROMPT_EN = """Compress the user message to the minimum. Keep actions, names, numbers, paths. Drop greetings, hedging, emotion. Respond ONLY with JSON: {{"compressed": "..."}}

Example:
<message>hi please implement the cache test on claude -p but use haiku first to save quota, thanks!</message>
{{"compressed": "implement cache test claude -p, haiku first"}}

<message>{message}</message>
"""

PROMPTS = {"pt": PROMPT_PT, "en": PROMPT_EN}

STOPWORDS = {
    "o", "a", "os", "as", "um", "uma", "uns", "umas",
    "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas",
    "que", "por", "para", "pra", "com", "e", "é",
    "the", "an", "of", "to", "in", "on", "at", "for", "and", "or",
    "is", "are", "was", "were", "be", "been", "by", "with", "as", "that",
}


def telegrafista(text: str) -> str:
    """Stage 2: drop common 1-token articles/preps. Empirically validated +10-30% reduction."""
    words = text.split()
    kept = [w for w in words if w.strip(".,!?;:").lower() not in STOPWORDS]
    out = " ".join(kept)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def llm_compress(text: str) -> str:
    """Stage 1: ask local LLM to compress. Returns original on any failure."""
    if not text or len(text) < 80:
        return text  # too short — skip LLM, just telegrafista
    prompt = PROMPTS.get(LANG, PROMPT_EN).format(message=text)
    payload = json.dumps({"model": MODEL, "prompt": prompt, "stream": False, "format": "json"})
    try:
        proc = subprocess.run(
            ["curl", "-sS", "--max-time", "30", OLLAMA_URL,
             "-d", payload, "-H", "Content-Type: application/json"],
            capture_output=True, text=True, timeout=35,
        )
        if proc.returncode != 0:
            return text
        outer = json.loads(proc.stdout.strip())
        inner_str = outer["response"].strip()
        # Tolerate markdown code fences (some models wrap JSON despite format=json)
        if inner_str.startswith("```"):
            inner_str = re.sub(r"^```(?:json)?\s*\n?", "", inner_str)
            inner_str = re.sub(r"\n?```\s*$", "", inner_str).strip()
        inner = json.loads(inner_str)
        val = inner.get("compressed", "")
        if isinstance(val, (dict, list)):
            val = json.dumps(val, ensure_ascii=False, separators=(",", ":"))
        compressed = str(val).strip()
        return compressed if compressed else text
    except Exception:
        return text


def compress(text: str) -> str:
    """Two-stage compression: LLM filter + telegrafista. Falls back to original on error."""
    if not text:
        return text
    s1 = llm_compress(text)
    return telegrafista(s1)


class Handler(BaseHTTPRequestHandler):
    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(n).decode()) if n else {}

    def _write_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        try:
            req = self._read_json()
        except json.JSONDecodeError:
            self._write_json({"error": "invalid json"}, 400)
            return

        hook = req.get("hook", "")

        if hook == "pre_worker_prompt":
            # Compress the user-facing prompt; preserve system_prompt as-is
            prompt = req.get("prompt", "")
            self._write_json({
                "prompt": compress(prompt),
                "system_prompt": req.get("system_prompt", ""),
            })
            return

        if hook == "pre_brain_prompt":
            # Compress the user capsule going to Brain
            user_capsule = req.get("user_capsule", "")
            self._write_json({
                "user_capsule": compress(user_capsule),
                "system_blocks": req.get("system_blocks", []),
            })
            return

        # Unknown hook — return passthrough
        self._write_json(req)

    def log_message(self, *args):  # silence default noisy logs
        pass


def main() -> None:
    print(f"burnless-compress plugin listening on :{PORT} (model={MODEL}, lang={LANG})")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
