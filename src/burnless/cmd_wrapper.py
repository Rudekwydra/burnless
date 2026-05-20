"""burnless cmd <command> — wrap shell, capsule large output via Haiku."""
from __future__ import annotations
import json
import re
import subprocess
import sys
import time
from pathlib import Path

SECRET_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "[MASKED_ANTHROPIC_KEY]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "[MASKED_OPENAI_KEY]"),
    (re.compile(r"ghp_[A-Za-z0-9]{30,}"), "[MASKED_GITHUB_PAT]"),
    (re.compile(r"xoxb-[A-Za-z0-9\-]+"), "[MASKED_SLACK_TOKEN]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"), "Bearer [MASKED]"),
    (re.compile(r"AWS_(?:ACCESS_KEY_ID|SECRET_ACCESS_KEY)=\S+"), "AWS_KEY=[MASKED]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[MASKED_AWS_KEY_ID]"),
]


def mask_secrets(text: str) -> str:
    for pat, repl in SECRET_PATTERNS:
        text = pat.sub(repl, text)
    return text


def _slug(command: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", command).strip("_")
    return s[:maxlen].lower()


def _save_raw(raw: str, command: str, project_root: Path) -> Path:
    cache_dir = project_root / ".burnless" / "cache" / "raw"
    cache_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    path = cache_dir / f"{ts}_{_slug(command)}.txt"
    path.write_text(raw, encoding="utf-8", errors="replace")
    return path


def _capsule_via_haiku(command: str, raw_path: Path, project_root: Path) -> dict:
    """Dispatch a bronze delegation that reads the raw file and emits envelope."""
    prompt = (
        f"Read {raw_path} (output of shell command: `{command}`). "
        "Emit ONLY a JSON object with keys: "
        "summary (one sentence), key_facts (array of strings), "
        "anomalies (array of strings, may be empty), counts (object), "
        "next_obvious_action (string). "
        "NO prose, NO markdown. Just the JSON object."
    )
    try:
        deleg = subprocess.run(
            ["burnless", "delegate", "--tier", "bronze", prompt],
            capture_output=True, text=True, cwd=str(project_root), timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return {"error": "delegate_unavailable", "detail": str(e)}
    if deleg.returncode != 0:
        return {"error": "delegate_failed", "stderr": deleg.stderr[:500]}
    m = re.search(r"\bd(\d+)\b", deleg.stdout)
    if not m:
        return {"error": "no_delegation_id", "stdout": deleg.stdout[:500]}
    deleg_id = f"d{m.group(1)}"
    try:
        subprocess.run(
            ["burnless", "run", deleg_id],
            capture_output=True, text=True, cwd=str(project_root), timeout=180,
        )
        cap = subprocess.run(
            ["burnless", "capsule", deleg_id],
            capture_output=True, text=True, cwd=str(project_root), timeout=10,
        )
    except subprocess.TimeoutExpired as e:
        return {"error": "run_timeout", "detail": str(e), "delegation_id": deleg_id}
    try:
        return {"capsule": json.loads(cap.stdout), "delegation_id": deleg_id}
    except Exception:
        return {"error": "capsule_parse_failed", "raw_capsule": cap.stdout[:500], "delegation_id": deleg_id}


def run_and_capsule(
    command: str,
    threshold: int = 4000,
    secret_mask: bool = True,
    project_root: Path | None = None,
) -> int:
    """Run command; if output > threshold, capsule via Haiku; print result; return exit_code."""
    if project_root is None:
        project_root = Path.cwd()
    proc = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=600)
    raw = (proc.stdout or "") + (proc.stderr or "")
    if secret_mask:
        raw = mask_secrets(raw)
    if len(raw) < threshold:
        sys.stdout.write(raw)
        return proc.returncode
    raw_path = _save_raw(raw, command, project_root)
    envelope = _capsule_via_haiku(command, raw_path, project_root)
    result = {
        "command": command,
        "exit_code": proc.returncode,
        "raw_size_chars": len(raw),
        "raw_ref": str(raw_path),
        "envelope": envelope,
    }
    sys.stdout.write(json.dumps(result, indent=2))
    sys.stdout.write("\n")
    return proc.returncode
