"""QTP-E: visual review hook tests."""
from __future__ import annotations

import base64
from pathlib import Path

from burnless import visual_review as vr


def test_is_visual_artifact_recognizes_extensions():
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
                ".pdf", ".pptx", ".html", ".PNG", ".JPG"):
        assert vr.is_visual_artifact(f"foo{ext}")


def test_is_visual_artifact_rejects_non_visual():
    for ext in (".py", ".md", ".json", ".txt", ".sh", ""):
        assert not vr.is_visual_artifact(f"foo{ext}")


def test_is_visual_artifact_handles_empty_and_none():
    assert not vr.is_visual_artifact("")
    assert not vr.is_visual_artifact(None)


def test_generate_thumbnail_for_real_png(tmp_path: Path):
    """Smoke: generate a 1×1 PNG, request 256×256 thumb."""
    try:
        from PIL import Image
    except ImportError:
        return  # skip if Pillow not available
    src = tmp_path / "tiny.png"
    Image.new("RGB", (10, 10), (255, 0, 0)).save(src)
    b64 = vr.generate_thumbnail(src, size=128)
    assert b64 is not None
    raw = base64.b64decode(b64)
    assert raw[:2] == b"\xff\xd8"  # JPEG magic


def test_generate_thumbnail_returns_none_for_missing_file(tmp_path: Path):
    assert vr.generate_thumbnail(tmp_path / "does-not-exist.png") is None


def test_attach_thumbnails_no_files_touched(tmp_path: Path):
    summary = {"status": "OK", "files_touched": []}
    out = vr.attach_thumbnails(summary, tmp_path)
    assert "visual_artifacts" not in out
    assert "visual_thumbnails" not in out


def test_attach_thumbnails_no_visuals_in_files_touched(tmp_path: Path):
    summary = {"status": "OK", "files_touched": ["a.py", "b.md"]}
    out = vr.attach_thumbnails(summary, tmp_path)
    assert "visual_artifacts" not in out


def test_attach_thumbnails_detects_png_in_files(tmp_path: Path):
    try:
        from PIL import Image
    except ImportError:
        return
    img_path = tmp_path / "out.png"
    Image.new("RGB", (50, 50), (0, 255, 0)).save(img_path)
    summary = {"status": "OK", "files_touched": [str(img_path)]}
    out = vr.attach_thumbnails(summary, tmp_path)
    assert "visual_artifacts" in out
    assert str(img_path) in out["visual_artifacts"]
    assert "visual_thumbnails" in out
    assert len(out["visual_thumbnails"]) == 1
    assert out["visual_thumbnails"][0]["path"] == str(img_path)
    assert out["visual_thumbnails"][0]["size"] == 256


def test_attach_thumbnails_relative_path_resolved(tmp_path: Path):
    try:
        from PIL import Image
    except ImportError:
        return
    img_path = tmp_path / "rel.png"
    Image.new("RGB", (20, 20), (0, 0, 255)).save(img_path)
    summary = {"status": "OK", "files_touched": ["rel.png"]}
    out = vr.attach_thumbnails(summary, tmp_path)
    assert "visual_thumbnails" in out


def test_attach_thumbnails_disabled_returns_unchanged(tmp_path: Path):
    summary = {"status": "OK", "files_touched": ["foo.png"]}
    out = vr.attach_thumbnails(summary, tmp_path, enabled=False)
    assert "visual_artifacts" not in out


def test_attach_thumbnails_thumbnails_off_attaches_paths_only(tmp_path: Path):
    summary = {"status": "OK", "files_touched": ["foo.png"]}
    out = vr.attach_thumbnails(summary, tmp_path, thumbnails=False)
    assert out.get("visual_artifacts") == ["foo.png"]
    assert "visual_thumbnails" not in out


def test_attach_thumbnails_respects_max_artifacts(tmp_path: Path):
    files = [f"img{i}.png" for i in range(10)]
    summary = {"status": "OK", "files_touched": files}
    out = vr.attach_thumbnails(summary, tmp_path, thumbnails=False, max_artifacts=3)
    assert len(out["visual_artifacts"]) == 3


def test_default_visual_review_enabled():
    from burnless import config
    vr_cfg = config.DEFAULT_CONFIG["visual_review"]
    assert vr_cfg["enabled"] is True
    assert vr_cfg["thumbnails"] is True
    assert vr_cfg["max_size"] == 256
    assert vr_cfg["max_artifacts"] == 5
