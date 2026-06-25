"""Burnless native ollama tool-calling worker. stdlib only."""
from __future__ import annotations
import json
import os
import subprocess
import urllib.request

from .compression import _strip_gemma_channels

_DEFAULT_SYSTEM = (
    "You are a Burnless Bronze Worker. Execute the spec by calling the provided "
    "filesystem/shell tools. When done, reply with a short final summary."
    "\n\nYou MUST use the tools to make changes — do not just describe them. "
    "Call escrever_arquivo to write files. Call executar_shell to run tests/verify. "
    "When the task is fully done, reply with a final plain-text summary and NO further tool calls."
)

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ler_arquivo",
            "description": "Read the contents of a file from disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "caminho": {"type": "string", "description": "Absolute or relative path of the file to read."}
                },
                "required": ["caminho"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escrever_arquivo",
            "description": "Write content to a file on disk (creates parent dirs as needed).",
            "parameters": {
                "type": "object",
                "properties": {
                    "caminho": {"type": "string", "description": "Absolute or relative path to write."},
                    "conteudo": {"type": "string", "description": "Full text content to write to the file."},
                },
                "required": ["caminho", "conteudo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "executar_shell",
            "description": "Run a shell command in the project root and return stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "comando": {"type": "string", "description": "Shell command to execute."}
                },
                "required": ["comando"],
            },
        },
    },
]


def is_ollama_tools_agent(cfg: dict) -> bool:
    return (cfg.get("provider") == "ollama-local") and bool(cfg.get("tools"))


def run_ollama_tools(
    model: str,
    prompt: str,
    *,
    cwd: str | None = None,
    system_prompt: str = "",
    timeout: int = 300,
    max_iters: int = 25,
    shell_timeout: int = 600,
    host: str = "http://localhost:11434",
) -> dict:
    """Run the agentic tool-calling loop against a local ollama model; return worker envelope dict."""
    effective_cwd = cwd or os.getcwd()
    effective_system = system_prompt or _DEFAULT_SYSTEM

    def _ler_arquivo(caminho: str) -> str:
        try:
            if not os.path.isabs(caminho):
                caminho = os.path.join(effective_cwd, caminho)
            with open(caminho, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as e:
            return f"ERRO: {e}"

    def _escrever_arquivo(caminho: str, conteudo: str) -> str:
        try:
            if not os.path.isabs(caminho):
                caminho = os.path.join(effective_cwd, caminho)
            os.makedirs(os.path.dirname(os.path.abspath(caminho)) or ".", exist_ok=True)
            with open(caminho, "w", encoding="utf-8") as f:
                f.write(conteudo)
            return f"OK: wrote {len(conteudo.encode())} bytes to {caminho}"
        except Exception as e:
            return f"ERRO: {e}"

    def _executar_shell(comando: str) -> str:
        try:
            r = subprocess.run(
                comando,
                shell=True,
                cwd=effective_cwd,
                capture_output=True,
                text=True,
                timeout=shell_timeout,
            )
            return f"rc={r.returncode}\nSTDOUT:\n{r.stdout[:4000]}\nSTDERR:\n{r.stderr[:2000]}"
        except subprocess.TimeoutExpired:
            return f"rc=124\nSTDOUT:\n\nSTDERR:\ntimeout after {shell_timeout}s"
        except Exception as e:
            return f"rc=-1\nSTDOUT:\n\nSTDERR:\n{e}"

    dispatch = {
        "ler_arquivo": lambda args: _ler_arquivo(args.get("caminho", "")),
        "escrever_arquivo": lambda args: _escrever_arquivo(
            args.get("caminho", ""), args.get("conteudo", "")
        ),
        "executar_shell": lambda args: _executar_shell(args.get("comando", "")),
    }

    messages: list[dict] = [
        {"role": "system", "content": effective_system},
        {"role": "user", "content": prompt},
    ]
    files_touched: list[str] = []
    validated: list[str] = []
    evidence: list[str] = [f"model: {model}", f"cwd: {effective_cwd}"]
    issues: list[str] = []
    final_text = ""
    status = "ERR"
    n = 0

    # Dual-protocol: default ollama (/api/chat); BURNLESS_LOCAL_API=llamacpp routes to a
    # llama-server + MTP daemon (OpenAI-compat /v1/chat/completions, default :11435). Reversible.
    api_mode = os.environ.get("BURNLESS_LOCAL_API", "ollama").lower()
    if api_mode == "llamacpp":
        local_host = os.environ.get("BURNLESS_LOCAL_HOST", "http://localhost:11435")
        endpoint = "/v1/chat/completions"
    else:
        local_host = host
        endpoint = "/api/chat"

    for n in range(1, max_iters + 1):
        if api_mode == "llamacpp":
            payload = {
                "model": model or "local",
                "messages": messages,
                "tools": _TOOLS,
                "stream": False,
                "temperature": 1.0,
                "top_p": 0.95,
            }
        else:
            payload = {
                "model": model,
                "messages": messages,
                "tools": _TOOLS,
                "stream": False,
                "keep_alive": os.environ.get("BURNLESS_OLLAMA_KEEPALIVE", "30m"),
                "options": {
                    "temperature": 1.0,
                    "top_p": 0.95,
                    "top_k": 64,
                    "num_ctx": int(os.environ.get("BURNLESS_OLLAMA_NUM_CTX", "32768")),
                },
            }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            local_host.rstrip("/") + endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            issues.append(f"iter {n} urlopen error: {e}")
            break

        if api_mode == "llamacpp":
            choices = resp_data.get("choices") or [{}]
            msg = choices[0].get("message", {}) or {}
        else:
            msg = resp_data.get("message", {})
        messages.append(msg)

        tool_calls = msg.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                handler = dispatch.get(name)
                result = handler(args) if handler else f"ERRO: tool '{name}' not found"
                if name == "escrever_arquivo" and result.startswith("OK:"):
                    path = args.get("caminho", "")
                    if path and path not in files_touched:
                        files_touched.append(path)
                if name == "executar_shell":
                    cmd = args.get("comando", "")
                    if cmd:
                        validated.append(cmd)
                evidence.append(f"tool={name} result={result[:200]}")
                if api_mode == "llamacpp":
                    messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result})
                else:
                    messages.append({"role": "tool", "content": result})
        else:
            raw = msg.get("content", "")
            final_text = _strip_gemma_channels(raw)
            status = "OK" if (files_touched or final_text) else "ERR"
            break
    else:
        issues.append(f"loop exhausted after {max_iters} iterations")

    evidence.append(f"iterations: {n}")

    if not final_text and not issues:
        final_text = "gemma tool-worker completed"

    if status != "OK" and not issues and (files_touched or final_text):
        status = "OK"

    return {
        "status": status,
        "summary": final_text[:180] if final_text else "gemma tool-worker completed",
        "files_touched": list(dict.fromkeys(files_touched)),
        "validated": validated,
        "evidence": evidence,
        "issues": issues,
        "next": "",
    }
