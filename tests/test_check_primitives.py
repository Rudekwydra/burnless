"""Tests for the deterministic check primitives (Wave 2 W3.3b)."""
import subprocess
from types import SimpleNamespace

import burnless.check_primitives as cp


def test_command_exit0(tmp_path):
    assert cp._check_command(SimpleNamespace(cmd="true", cwd=str(tmp_path), timeout=10)) == 0
    assert cp._check_command(SimpleNamespace(cmd="false", cwd=str(tmp_path), timeout=10)) == 1


def test_file_size_bounds(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("12345")
    assert cp._check_file_size(SimpleNamespace(path=str(f), min=1, max=10)) == 0
    assert cp._check_file_size(SimpleNamespace(path=str(f), min=10, max=None)) == 1
    assert cp._check_file_size(SimpleNamespace(path=str(f), min=None, max=3)) == 1
    assert cp._check_file_size(SimpleNamespace(path=str(f), min=None, max=None)) == 2
    assert cp._check_file_size(SimpleNamespace(path=str(tmp_path / "nope"), min=1, max=2)) == 2


def test_mtime_after(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert cp._check_mtime_after(SimpleNamespace(path=str(f), timestamp="2000-01-01T00:00:00")) == 0
    assert cp._check_mtime_after(SimpleNamespace(path=str(f), timestamp="2999-01-01T00:00:00")) == 1
    assert cp._check_mtime_after(SimpleNamespace(path=str(f), timestamp="not-a-date")) == 2
    assert cp._check_mtime_after(SimpleNamespace(path=str(tmp_path / "nope"), timestamp="2000-01-01T00:00:00")) == 2


def test_git_clean(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    assert cp._check_git_clean(SimpleNamespace(cwd=str(repo), allow_untracked=[])) == 0
    (repo / "junk.log").write_text("x")
    assert cp._check_git_clean(SimpleNamespace(cwd=str(repo), allow_untracked=[])) == 1
    assert cp._check_git_clean(SimpleNamespace(cwd=str(repo), allow_untracked=["*.log"])) == 0
    assert cp._check_git_clean(SimpleNamespace(cwd=str(tmp_path / "notrepo"), allow_untracked=[])) == 2


def test_cli_registration():
    import argparse
    from burnless.check_primitives import register_check_parser
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    register_check_parser(sub)
    args = parser.parse_args(["check", "command", "true"])
    assert args.func(args) == 0
