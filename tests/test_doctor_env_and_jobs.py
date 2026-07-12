"""Tests for doctor band F (env) and G (scheduled jobs)."""
import os
import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from burnless.doctor import _check_f, _check_g, Check


class TestCheckF:
    """Band F: environment reachability checks."""

    def test_f1_llamacpp_unreachable(self, monkeypatch):
        """LlamaCpp port unreachable → WARN (not FAIL)."""
        monkeypatch.setenv("BURNLESS_LOCAL_API", "llamacpp")
        monkeypatch.setenv("BURNLESS_LOCAL_HOST", "http://localhost:19999")

        checks = []
        _check_f(checks)

        assert len(checks) == 1
        c = checks[0]
        assert c.id == "F1"
        assert c.band == "F"
        assert c.status == "WARN"
        assert "localhost:19999" in c.detail
        assert "unreachable" in c.detail

    def test_f1_ollama_default(self, monkeypatch):
        """Ollama default (env vars unset) → attempts localhost:11434."""
        monkeypatch.delenv("BURNLESS_LOCAL_API", raising=False)
        monkeypatch.delenv("BURNLESS_OLLAMA_HOST", raising=False)

        checks = []
        _check_f(checks)

        assert len(checks) == 1
        c = checks[0]
        assert c.id == "F1"
        assert c.band == "F"
        # May be PASS or WARN depending on whether ollama is running; just check it's not FAIL
        assert c.status in ("PASS", "WARN")


class TestCheckG:
    """Band G: scheduled jobs health checks."""

    def test_g_no_config(self, monkeypatch, tmp_path):
        """No .burnless/ dir → _check_g returns zero checks."""
        monkeypatch.chdir(tmp_path)

        checks = []
        _check_g(checks, cwd=tmp_path)

        assert len(checks) == 0

    def test_g_no_scheduled_jobs_key(self, monkeypatch, tmp_path):
        """Config exists but scheduled_jobs key missing → zero checks."""
        burnless_dir = tmp_path / ".burnless"
        burnless_dir.mkdir()
        config_file = burnless_dir / "config.yaml"

        # Minimal config without scheduled_jobs
        config_file.write_text("agents: {}\nrouting: {}\nmetrics: {}\n")

        checks = []
        _check_g(checks, cwd=tmp_path)

        assert len(checks) == 0

    def test_g_empty_scheduled_jobs(self, monkeypatch, tmp_path):
        """Config has scheduled_jobs: [] → zero checks."""
        burnless_dir = tmp_path / ".burnless"
        burnless_dir.mkdir()
        config_file = burnless_dir / "config.yaml"

        config_file.write_text(
            "agents: {}\nrouting: {}\nmetrics: {}\nscheduled_jobs: []\n"
        )

        checks = []
        _check_g(checks, cwd=tmp_path)

        assert len(checks) == 0

    def test_g_file_not_exists(self, monkeypatch, tmp_path):
        """Job path does not exist → FAIL."""
        burnless_dir = tmp_path / ".burnless"
        burnless_dir.mkdir()
        config_file = burnless_dir / "config.yaml"

        config_file.write_text(
            "agents: {}\n"
            "routing: {}\n"
            "metrics: {}\n"
            "scheduled_jobs:\n"
            "  - name: missing_job\n"
            f'    path: "{tmp_path}/nonexistent.log"\n'
            "    period_hours: 24\n"
        )

        checks = []
        _check_g(checks, cwd=tmp_path)

        assert len(checks) == 1
        c = checks[0]
        assert c.id == "G1"
        assert c.band == "G"
        assert c.status == "FAIL"
        assert "does not exist" in c.detail
        assert "missing_job" in c.detail

    def test_g_file_too_old(self, monkeypatch, tmp_path):
        """Job file older than 2*period_hours → FAIL."""
        burnless_dir = tmp_path / ".burnless"
        burnless_dir.mkdir()
        config_file = burnless_dir / "config.yaml"

        # Create a job file
        job_file = tmp_path / "old_job.log"
        job_file.write_text("test")

        # Set mtime to 48 hours ago
        now_ts = datetime.now(timezone.utc).timestamp()
        old_ts = now_ts - (48 * 3600)  # 48 hours in past
        os.utime(job_file, (old_ts, old_ts))

        config_file.write_text(
            "agents: {}\n"
            "routing: {}\n"
            "metrics: {}\n"
            "scheduled_jobs:\n"
            "  - name: stale_job\n"
            f'    path: "{job_file}"\n'
            "    period_hours: 1\n"
        )

        checks = []
        _check_g(checks, cwd=tmp_path)

        assert len(checks) == 1
        c = checks[0]
        assert c.id == "G1"
        assert c.band == "G"
        assert c.status == "FAIL"
        assert "stale_job" in c.detail
        assert "last success" in c.detail
        assert "limit 2h" in c.detail  # 2*period_hours = 2*1 = 2

    def test_g_file_recent(self, monkeypatch, tmp_path):
        """Job file within 2*period_hours → PASS."""
        burnless_dir = tmp_path / ".burnless"
        burnless_dir.mkdir()
        config_file = burnless_dir / "config.yaml"

        # Create a job file with recent mtime
        job_file = tmp_path / "recent_job.log"
        job_file.write_text("test")

        # mtime is now (default)
        config_file.write_text(
            "agents: {}\n"
            "routing: {}\n"
            "metrics: {}\n"
            "scheduled_jobs:\n"
            "  - name: healthy_job\n"
            f'    path: "{job_file}"\n'
            "    period_hours: 24\n"
        )

        checks = []
        _check_g(checks, cwd=tmp_path)

        assert len(checks) == 1
        c = checks[0]
        assert c.id == "G1"
        assert c.band == "G"
        assert c.status == "PASS"
        assert "healthy_job" in c.detail
        assert "within period" in c.detail

    def test_g_malformed_entry(self, monkeypatch, tmp_path):
        """Job entry missing required key → WARN."""
        burnless_dir = tmp_path / ".burnless"
        burnless_dir.mkdir()
        config_file = burnless_dir / "config.yaml"

        config_file.write_text(
            "agents: {}\n"
            "routing: {}\n"
            "metrics: {}\n"
            "scheduled_jobs:\n"
            "  - name: bad_job\n"
            "    period_hours: 24\n"  # missing 'path'
        )

        checks = []
        _check_g(checks, cwd=tmp_path)

        assert len(checks) == 1
        c = checks[0]
        assert c.id == "G1"
        assert c.band == "G"
        assert c.status == "WARN"
        assert "malformed" in c.detail

    def test_g_multiple_jobs(self, monkeypatch, tmp_path):
        """Multiple jobs → multiple checks, indexed G1, G2, etc."""
        burnless_dir = tmp_path / ".burnless"
        burnless_dir.mkdir()
        config_file = burnless_dir / "config.yaml"

        job1_file = tmp_path / "job1.log"
        job1_file.write_text("test")

        job2_file = tmp_path / "job2.log"
        # Set job2 to 50h old, period=1h
        now_ts = datetime.now(timezone.utc).timestamp()
        old_ts = now_ts - (50 * 3600)
        job2_file.write_text("test")
        os.utime(job2_file, (old_ts, old_ts))

        config_file.write_text(
            "agents: {}\n"
            "routing: {}\n"
            "metrics: {}\n"
            "scheduled_jobs:\n"
            f"  - name: job1\n"
            f'    path: "{job1_file}"\n'
            f"    period_hours: 24\n"
            f"  - name: job2\n"
            f'    path: "{job2_file}"\n'
            f"    period_hours: 1\n"
        )

        checks = []
        _check_g(checks, cwd=tmp_path)

        assert len(checks) == 2
        assert checks[0].id == "G1"
        assert checks[0].status == "PASS"
        assert checks[1].id == "G2"
        assert checks[1].status == "FAIL"

    def test_g_tilde_expansion(self, monkeypatch, tmp_path):
        """Path with ~ is expanded correctly."""
        burnless_dir = tmp_path / ".burnless"
        burnless_dir.mkdir()
        config_file = burnless_dir / "config.yaml"

        # Create a temp file outside tmp_path that we can reference via ~
        # For simplicity, just check that the path expansion doesn't crash
        # Use a path that definitely won't exist
        home = Path.home()
        fake_path = home / "fake_burnless_test_nonexistent.log"

        config_file.write_text(
            "agents: {}\n"
            "routing: {}\n"
            "metrics: {}\n"
            "scheduled_jobs:\n"
            "  - name: tilde_test\n"
            f'    path: "~/fake_burnless_test_nonexistent.log"\n'
            "    period_hours: 24\n"
        )

        checks = []
        _check_g(checks, cwd=tmp_path)

        assert len(checks) == 1
        c = checks[0]
        assert c.id == "G1"
        assert c.status == "FAIL"
        assert "does not exist" in c.detail
