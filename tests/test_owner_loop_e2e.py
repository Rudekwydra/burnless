"""E2E coverage for async owner-loop refinement via the refine-owner job."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from burnless import epochs_v2


REFINED_MARKDOWN = """> ordem: documento vivo (living-doc v2) consolidado por slot — entradas mais NOVAS primeiro em cada secao

## Foco atual
- task alpha owner-loop baseline
- task beta owner-loop baseline

## Threads abertas
- pending beta owner-loop check
- pending alpha owner-loop check

## Decisões
- decision beta owner-loop accepted
- decision alpha owner-loop accepted

## Contracts
- /tmp/beta.py: owner_loop_beta_contract
- /tmp/alpha.py: owner_loop_alpha_contract

## Refs
- d102 beta-owner-ref
- d101 alpha-owner-ref

## Sources
- chat_b, chat_a
"""


def _burnless_command(env):
    module_cmd = [sys.executable, "-m", "burnless"]
    check = subprocess.run(
        module_cmd + ["--help"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        timeout=5,
    )
    if check.returncode == 0:
        return module_cmd

    burnless = shutil.which("burnless")
    if burnless:
        return [burnless]

    pytest.fail("burnless is not invokable via `python -m burnless` or console script")


def test_refine_owner_async_job_writes_real_refined_seed_with_fake_claude():
    proc = None

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        bindir = tmp / "bin"
        bindir.mkdir()
        fake_claude = bindir / "claude"
        fake_claude.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            "import sys\n"
            "sys.stdin.read()\n"
            f"print(json.dumps({{'result': {REFINED_MARKDOWN!r}}}))\n",
            encoding="utf-8",
        )
        fake_claude.chmod(0o755)

        root = tmp / "root"
        root.mkdir()
        burnless_root = root / ".burnless"
        epochs_dir = burnless_root / "epochs"
        epochs_dir.mkdir(parents=True)
        (burnless_root / "config.yaml").write_text(
            "encoder:\n"
            "  provider: anthropic\n"
            "  model: claude-test\n"
            "epochs:\n"
            "  enabled: true\n",
            encoding="utf-8",
        )

        chat_a = "chat_a"
        chat_b = "chat_b"
        epochs_v2.living_path(root, chat_a).parent.mkdir(parents=True, exist_ok=True)
        epochs_v2.living_path(root, chat_a).write_text(
            "## Foco atual\n"
            "- task alpha owner-loop baseline\n\n"
            "## Threads abertas\n"
            "- pending alpha owner-loop check\n\n"
            "## Decisões\n"
            "- decision alpha owner-loop accepted\n\n"
            "## Contracts\n"
            "- /tmp/alpha.py: owner_loop_alpha_contract\n\n"
            "## Refs\n"
            "- d101 alpha-owner-ref\n",
            encoding="utf-8",
        )
        time.sleep(0.02)
        epochs_v2.living_path(root, chat_b).parent.mkdir(parents=True, exist_ok=True)
        epochs_v2.living_path(root, chat_b).write_text(
            "## Foco atual\n"
            "- task beta owner-loop baseline\n\n"
            "## Threads abertas\n"
            "- pending beta owner-loop check\n\n"
            "## Decisões\n"
            "- decision beta owner-loop accepted\n\n"
            "## Contracts\n"
            "- /tmp/beta.py: owner_loop_beta_contract\n\n"
            "## Refs\n"
            "- d102 beta-owner-ref\n",
            encoding="utf-8",
        )

        env = {
            **os.environ,
            "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
            "BURNLESS_EPOCH_V2": "1",
        }
        cmd = _burnless_command(env) + [
            "epoch",
            "refine-owner",
            "--chat-id",
            "chat_current_async",
            "--root",
            str(root),
        ]
        proc = subprocess.Popen(cmd, env=env)

        cache_path = root / ".burnless" / "epochs" / "_rolling" / "refined_seed.json"
        log_path = root / ".burnless" / "owner_loop.jsonl"
        deadline = time.time() + 20
        while time.time() < deadline:
            if cache_path.exists():
                break
            if proc.poll() is not None and proc.returncode != 0:
                break
            time.sleep(0.1)

        if proc.poll() is None:
            proc.wait(timeout=5)

        if not cache_path.exists():
            log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else "<missing>"
            pytest.fail(f"refined_seed.json was not written; owner_loop.jsonl:\n{log_text}")

        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        seed_md = payload["seed_md"].strip()
        assert seed_md
        assert "task alpha owner-loop baseline" in seed_md
        assert "task beta owner-loop baseline" in seed_md

        assert log_path.exists(), "owner-loop should log refinement result"
        events = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any(event.get("result") == "written" for event in events)

    if proc is not None and proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=5)
