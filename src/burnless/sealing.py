from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def _first_focus_line(living_md: str) -> str:
    from .epochs_v2 import parse_living_v3

    parsed = parse_living_v3(living_md)
    focus = parsed.get("Foco atual") or []
    return focus[0].strip() if focus else ""


def _forgetless_binary() -> str | None:
    path = shutil.which("forgetless")
    if path:
        return path
    fallback = Path.home() / ".local" / "bin" / "forgetless"
    return str(fallback) if fallback.exists() else None


def seal_epoch(root, host: str, host_session_id: str) -> dict[str, Any]:
    """Seal the current living_md into a Forgetless capsule (memória fria).

    Fail-open: any error here must never affect the hot checkpoint. Always
    returns a status dict, never raises.
    """
    try:
        from . import recovery

        root_path = recovery._root_path(root)
        checkpoint = recovery.read_checkpoint(root_path, host, host_session_id)
        living_md = (checkpoint or {}).get("living_md") or ""
        if not living_md.strip():
            return {"status": "seal_skipped", "reason": "empty_living_md"}

        forgetless_bin = _forgetless_binary()
        if not forgetless_bin:
            return {"status": "seal_skipped", "reason": "forgetless_not_found"}

        project = Path(root_path).parent.name
        sid8 = host_session_id[:8]
        capsule_name = f"epoch-{project}-{sid8}"
        summary = _first_focus_line(living_md) or f"epoch capsule for {host_session_id}"

        body_parts = [
            living_md.strip(),
            "",
            "---",
            f"host_session_id: {host_session_id}",
            f"generation: {checkpoint.get('generation')}",
            f"applied_through: {checkpoint.get('applied_through')}",
            f"journal_head: {checkpoint.get('journal_head')}",
        ]
        body = "\n".join(str(p) for p in body_parts)

        body_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        )
        try:
            body_file.write(body)
            body_file.close()

            get_result = subprocess.run(
                [forgetless_bin, "get", capsule_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            exists = get_result.returncode == 0

            cmd = [
                forgetless_bin,
                "update" if exists else "new",
                capsule_name,
                "--summary",
                summary,
                "--body-file",
                body_file.name,
                "--tag",
                "burnless-epoch",
                "--tag",
                project,
            ]
            if exists:
                cmd.append("--append")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                return {
                    "status": "seal_skipped",
                    "reason": "forgetless_error",
                    "stderr": (result.stderr or "")[:500],
                }
            return {
                "status": "sealed",
                "capsule": capsule_name,
                "mode": "update" if exists else "new",
            }
        finally:
            try:
                os.unlink(body_file.name)
            except OSError:
                pass
    except Exception as exc:
        return {"status": "seal_skipped", "reason": str(exc)}
