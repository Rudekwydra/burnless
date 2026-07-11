import pytest
import tempfile
import json
import threading
import time
from pathlib import Path

from burnless.epochs_v2 import apply_capture, living_path, state_path


def test_state_json_atomic_and_valid():
    """Test that state.json is valid JSON after apply_capture and turn counter increments."""
    with tempfile.TemporaryDirectory() as tmp_root:
        tmp_root = Path(tmp_root)
        chat_id = "chat1"
        exchange = f"user: check /Users/roberto/antigravity/test.py\nassistant: found it" + "x" * 250

        def fake_rewriter(prompt: str) -> str:
            return "# Foco atual\n- test entry\n- another line"

        result = apply_capture(tmp_root, chat_id, exchange, rewriter=fake_rewriter)

        assert result.exists()

        state_file = state_path(tmp_root, chat_id)
        assert state_file.exists(), f"state file not found at {state_file}"

        state_content = state_file.read_text(encoding='utf-8')
        state = json.loads(state_content)

        assert state.get('turn') == 1
        assert isinstance(state, dict)


def test_concurrent_capture_no_lost_turn():
    """Test that concurrent apply_capture calls on same chat_id don't lose turn increments."""
    with tempfile.TemporaryDirectory() as tmp_root:
        tmp_root = Path(tmp_root)
        chat_id = "chat_concurrent"

        results = []
        errors = []

        def slow_rewriter(prompt: str) -> str:
            time.sleep(0.2)
            return "# Foco atual\n- concurrent entry\n- test"

        def capture_thread(exchange_text: str):
            try:
                result = apply_capture(tmp_root, chat_id, exchange_text, rewriter=slow_rewriter)
                results.append(result)
            except Exception as e:
                errors.append(e)

        exchange_base = f"user: check /Users/roberto/file{0}.py\nassistant: found\n" + "x" * 250
        threads = [
            threading.Thread(target=capture_thread, args=(exchange_base + f"msg{i}",))
            for i in range(2)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Unexpected errors: {errors}"

        state_file = state_path(tmp_root, chat_id)
        assert state_file.exists(), f"state file not found at {state_file}"

        state_content = state_file.read_text(encoding='utf-8')
        state = json.loads(state_content)

        assert state.get('turn') == 2, f"Expected turn==2, got {state.get('turn')}"


def test_rewriter_exception_preserves_living_and_logs(monkeypatch):
    """Test that when rewriter raises, living doc is preserved and error is logged."""
    with tempfile.TemporaryDirectory() as tmp_root:
        tmp_root = Path(tmp_root)
        tmp_hook_log = Path(tempfile.mkdtemp()) / "hook_errors.log"

        try:
            from burnless import recovery as recovery_mod

            monkeypatch.setattr(recovery_mod, "_hook_error_log_path", lambda root: tmp_hook_log)

            chat_id = "chat_error"

            living = living_path(tmp_root, chat_id)
            living.parent.mkdir(parents=True, exist_ok=True)
            living.write_text("CONTEUDO_PRESERVADO", encoding='utf-8')

            def failing_rewriter(prompt: str) -> str:
                raise RuntimeError("boom")

            exchange_base = f"user: check /Users/roberto/file.py\nassistant: found\n" + "x" * 250
            result = apply_capture(tmp_root, chat_id, exchange_base, rewriter=failing_rewriter)

            assert living.exists(), f"living doc not found at {living}"
            assert living.read_text(encoding='utf-8') == "CONTEUDO_PRESERVADO"

            assert tmp_hook_log.exists(), f"hook_errors.log not found at {tmp_hook_log}"

            log_content = tmp_hook_log.read_text(encoding='utf-8')
            assert "apply_capture" in log_content, f"'apply_capture' not found in {log_content}"
        finally:
            import shutil
            shutil.rmtree(tmp_hook_log.parent, ignore_errors=True)
