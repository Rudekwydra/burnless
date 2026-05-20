"""Tests for cmd_wrapper."""
import json
from burnless.cmd_wrapper import mask_secrets, run_and_capsule, _slug


def test_mask_openai_key():
    out = mask_secrets("token=sk-abcdef0123456789ABCDEFGHIJ")
    assert "sk-abcdef" not in out
    assert "[MASKED_OPENAI_KEY]" in out


def test_mask_anthropic_key():
    out = mask_secrets("export ANTHROPIC_API_KEY=sk-ant-api03-abcdef0123456789ABCDEF")
    assert "sk-ant-api03" not in out
    assert "[MASKED_ANTHROPIC_KEY]" in out


def test_mask_bearer():
    out = mask_secrets("Authorization: Bearer eyJabc.def.ghi")
    assert "eyJabc" not in out
    assert "Bearer [MASKED]" in out


def test_mask_github_pat():
    out = mask_secrets("token=ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    assert "ghp_abcdef" not in out
    assert "[MASKED_GITHUB_PAT]" in out


def test_slug_safe():
    assert _slug("git log --all") == "git_log_all"
    long = _slug("a" * 100)
    assert long.startswith("a" * 40)
    assert len(long) <= 40


def test_under_threshold_prints_raw(capsys, tmp_path):
    rc = run_and_capsule("echo hello", threshold=1000, project_root=tmp_path)
    captured = capsys.readouterr()
    assert "hello" in captured.out
    assert rc == 0


def test_over_threshold_saves_raw(capsys, tmp_path):
    rc = run_and_capsule("echo hello", threshold=1, project_root=tmp_path)
    captured = capsys.readouterr()
    cache_dir = tmp_path / ".burnless" / "cache" / "raw"
    assert cache_dir.exists()
    files = list(cache_dir.glob("*.txt"))
    assert len(files) == 1
    assert "hello" in files[0].read_text()
    payload = json.loads(captured.out)
    assert payload["command"] == "echo hello"
    assert payload["exit_code"] == 0
    assert payload["raw_size_chars"] >= 1
    assert "raw_ref" in payload
    assert "envelope" in payload
