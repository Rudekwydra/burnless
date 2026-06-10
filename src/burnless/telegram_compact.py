from __future__ import annotations
import json
import subprocess
import urllib.request
import urllib.error


def compact_to_telegram(text, *, project_root=None, timeout=8):
    "Returns a validated JSON telegram string {i,r,m} or None (fail-open)."
    try:
        from . import config, paths
        try:
            root_dir = project_root if project_root else paths.require_root()
            cfg = config.load(paths.paths_for(root_dir)["config"])
        except Exception:
            cfg = {}
        enc = cfg.get("encoder") or {}
        provider = (enc.get("provider") or "anthropic").strip()
        model = enc.get("model") or config.DEFAULT_TIER_MODELS["bronze"]
    except Exception:
        return None

    if provider == "passthrough" or model == "passthrough":
        return None

    prompt = (
        "Você é compactador telegrafo. Reescreva o input do user em JSON puro com chaves: "
        "i (intent verbo imperativo), r (refs: paths/IDs/nomes), m (markers: URG|DEC|HYPE|PERS "
        "se aplicável, senão omita). MÁX 30 tokens. Output JSON apenas, sem prosa, sem markdown "
        "fence.\n\n[USER INPUT]\n" + text
    )

    try:
        if provider == "ollama-local":
            data = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
            req = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read())
            out = body["response"]
            from .compression import _strip_gemma_channels
            out = _strip_gemma_channels(out)
        else:
            try:
                from .warm_session import _claude_binary
                claude_bin = _claude_binary() or "claude"
            except Exception:
                claude_bin = "claude"
            result = subprocess.run(
                [claude_bin, "-p", "--model", model, "--permission-mode", "bypassPermissions",
                 "--allowedTools", "", "--output-format", "json"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            data = json.loads(result.stdout)
            out = data["result"]

        out = out.strip()
        if out.startswith("```"):
            lines = out.split("\n")
            out = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        parsed = json.loads(out)
        return json.dumps(parsed, separators=(",", ":"))
    except Exception:
        return None


def main():
    import sys
    text = sys.stdin.read()
    result = compact_to_telegram(text)
    print(result if result is not None else "")


if __name__ == "__main__":
    main()
