from pathlib import Path
import tempfile
from burnless.cli import _with_runtime_context, _TELEGRAPHIC_OUTPUT_HINT


def _ctx_args(tmp: str):
    project = Path(tmp) / "project"
    burnless = Path(tmp) / "project" / ".burnless"
    project.mkdir(parents=True, exist_ok=True)
    burnless.mkdir(parents=True, exist_ok=True)
    return project, burnless


def test_legacy_layout_includes_telegraphic_hint():
    with tempfile.TemporaryDirectory() as tmp:
        project, burnless = _ctx_args(tmp)
        out = _with_runtime_context(
            "do thing X",
            project_root=project,
            burnless_root=burnless,
            cache_prefix=False,
        )
        assert "telegraphic" in out.lower()
        assert "telegráfico" in out.lower() or "telegraf" in out.lower()
        assert "imp=implementar" in out
        assert "do thing X" in out


def test_cache_prefix_layout_includes_telegraphic_hint():
    with tempfile.TemporaryDirectory() as tmp:
        project, burnless = _ctx_args(tmp)
        out = _with_runtime_context(
            "do thing Y",
            project_root=project,
            burnless_root=burnless,
            cache_prefix=True,
        )
        assert "telegraphic" in out.lower()
        assert "Output style" in out
        assert "do thing Y" in out


def test_hint_preserves_json_envelope_requirement():
    assert "JSON envelope" in _TELEGRAPHIC_OUTPUT_HINT
    assert "files_touched" in _TELEGRAPHIC_OUTPUT_HINT
    assert "evidence" in _TELEGRAPHIC_OUTPUT_HINT


def test_hint_forbids_telegraphing_evidence():
    assert "NUNCA" in _TELEGRAPHIC_OUTPUT_HINT
    assert "evidence" in _TELEGRAPHIC_OUTPUT_HINT.lower()


def test_chain_manifest_still_appears_before_telegraphic():
    with tempfile.TemporaryDirectory() as tmp:
        project, burnless = _ctx_args(tmp)
        cap = burnless / "capsules" / "d001.json"
        cap.parent.mkdir(parents=True, exist_ok=True)
        cap.write_text("{}", encoding="utf-8")
        out = _with_runtime_context(
            "task",
            project_root=project,
            burnless_root=burnless,
            chain=["d001"],
            cache_prefix=True,
        )
        assert "Lazy Context Manifest" in out
        assert out.index("Lazy Context Manifest") < out.index("telegraphic")
