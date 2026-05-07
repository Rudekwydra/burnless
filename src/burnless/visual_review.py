"""Visual review hook (QTP-E).

When the worker's files_touched includes a visual deliverable
(png/jpg/pdf/pptx/svg/html), attach a 256×256 base64 thumbnail to the
audit JSON so the operator can scan for "obviously wrong" output at a
glance — without opening N files manually.

Origin: QTP_OPERATIONAL_TEST_2026-05-06.md issue 5. Worker + LLM
auditor were both happy with deliverables that were "design feio,
simplório" — file present, size OK, prose OK, but visual quality bad.
Filesystem audit (QTP-A) catches missing files; this hook catches
glanceable failures in things that filesystem can't see.

Tool chain (try in order):
  1. Pillow (Python PIL) — preferred, portable
  2. sips (macOS built-in) — fallback when Pillow missing
  3. None — attach path only, operator opens manually

Stays out of the way: if no tool available + visual file detected,
audit is unchanged. No hard dep on Pillow.
"""
from __future__ import annotations

import base64
import io
import shutil
import subprocess
from pathlib import Path

VISUAL_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
               ".pdf", ".pptx", ".html"}


def is_visual_artifact(path: str) -> bool:
    """True if path's extension marks it as a visual deliverable."""
    if not path:
        return False
    return Path(path).suffix.lower() in VISUAL_EXTS


def _pillow_thumbnail(src: Path, size: int) -> bytes | None:
    """Return JPEG-encoded thumbnail bytes via Pillow, or None on failure."""
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return None
    try:
        img = Image.open(src)
        img.thumbnail((size, size))
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            img = bg
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=70)
        return out.getvalue()
    except Exception:
        return None


def _sips_thumbnail(src: Path, size: int) -> bytes | None:
    """macOS sips fallback. Writes to a temp path then reads bytes."""
    if not shutil.which("sips"):
        return None
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
        tmp = Path(tf.name)
    try:
        r = subprocess.run(
            ["sips", "-s", "format", "jpeg", "-Z", str(size), str(src), "--out", str(tmp)],
            capture_output=True, timeout=10,
        )
        if r.returncode == 0 and tmp.exists():
            return tmp.read_bytes()
    except Exception:
        pass
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
    return None


def generate_thumbnail(path: Path, size: int = 256) -> str | None:
    """Return base64-encoded JPEG thumbnail string, or None if unavailable."""
    if not path.exists():
        return None
    data = _pillow_thumbnail(path, size) or _sips_thumbnail(path, size)
    if data is None:
        return None
    return base64.b64encode(data).decode("ascii")


def attach_thumbnails(
    summary: dict,
    cwd: Path,
    *,
    enabled: bool = True,
    thumbnails: bool = True,
    max_size: int = 256,
    max_artifacts: int = 5,
) -> dict:
    """Detect visual artifacts in summary['files_touched'] and attach thumbs.

    Modifies summary in place AND returns it. New keys:
      - visual_artifacts: list of paths recognized as visual
      - visual_thumbnails: [{path, thumb_b64, size}] if thumbnails enabled
    """
    if not enabled:
        return summary
    files = summary.get("files_touched") or []
    if not isinstance(files, list):
        return summary

    visuals: list[str] = []
    for f in files:
        if isinstance(f, str) and is_visual_artifact(f):
            visuals.append(f)
        if len(visuals) >= max_artifacts:
            break

    if not visuals:
        return summary

    summary["visual_artifacts"] = visuals
    if not thumbnails:
        return summary

    thumbs: list[dict] = []
    for path_str in visuals:
        p = Path(path_str)
        if not p.is_absolute():
            p = cwd / p
        b64 = generate_thumbnail(p, size=max_size)
        if b64:
            thumbs.append({"path": path_str, "thumb_b64": b64, "size": max_size})
    if thumbs:
        summary["visual_thumbnails"] = thumbs
    return summary
