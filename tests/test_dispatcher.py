from __future__ import annotations

import json
from pathlib import Path

from burnless.codec.cipher import encode as cipher_encode
from burnless.codec.cipher import decode as cipher_decode
from burnless.maestro import dispatcher


def test_worker_system_prompt_falls_back_to_glossary_and_role(tmp_path, monkeypatch):
    project_root = tmp_path
    role_dir = project_root / "_design" / "maestro_v1"
    role_dir.mkdir(parents=True)
    (role_dir / "glossary.md").write_text("glossary text", encoding="utf-8")
    (role_dir / "worker_role.md").write_text("worker role", encoding="utf-8")

    monkeypatch.setattr(dispatcher, "load_glossary", lambda root: "glossary text")
    monkeypatch.setattr(dispatcher.Path, "home", classmethod(lambda cls: tmp_path / "missing-home"))

    assert dispatcher._worker_system_prompt(project_root) == "glossary text\n\n---\n\nworker role"


def test_worker_system_prompt_uses_cloud_emulator_when_present(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    role_dir = project_root / "_design" / "maestro_v1"
    role_dir.mkdir(parents=True)
    (role_dir / "worker_role.md").write_text("fallback role", encoding="utf-8")

    home_dir = tmp_path / "home"
    cloud_dir = home_dir / ".burnless"
    cloud_dir.mkdir(parents=True)
    plaintext = "dynamic system prompt"
    (cloud_dir / "cloud_emulator.py").write_text(
        "import os\n\n"
        "class CloudEmulator:\n"
        "    def fetch_system_prompt(self):\n"
        "        assert os.environ['BURNLESS_SESSION_ID']\n"
        f"        return {{'plaintext_wrapper': 'wrapper', 'ciphertext_block': {cipher_encode(plaintext, '00' * 32)!r}, 'key_hex': {'00' * 32!r}}}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(dispatcher, "load_glossary", lambda root: "ignored")
    monkeypatch.setattr(dispatcher.Path, "home", classmethod(lambda cls: home_dir))

    assert dispatcher._worker_system_prompt(project_root) == plaintext


def test_load_cloud_emulator_prompt_round_trips_ciphertext(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    role_dir = project_root / "_design" / "maestro_v1"
    role_dir.mkdir(parents=True)
    (role_dir / "glossary.md").write_text("glossary text", encoding="utf-8")
    (role_dir / "worker_role.md").write_text("worker role", encoding="utf-8")

    home_dir = tmp_path / "home"
    cloud_dir = home_dir / ".burnless"
    cloud_dir.mkdir(parents=True)
    monkeypatch.setattr(dispatcher.Path, "home", classmethod(lambda cls: home_dir))

    module_path = cloud_dir / "cloud_emulator.py"
    module_path.write_text(
        Path("/Users/roberto/.burnless/cloud_emulator.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    decrypted = dispatcher._load_cloud_emulator_prompt(module_path, project_root)
    assert decrypted is not None

    payload = {}
    previous_project_root = dispatcher.os.environ.get("BURNLESS_PROJECT_ROOT")
    previous_session_id = dispatcher.os.environ.get("BURNLESS_SESSION_ID")
    dispatcher.os.environ["BURNLESS_PROJECT_ROOT"] = str(project_root)
    dispatcher.os.environ["BURNLESS_SESSION_ID"] = "test-session"
    try:
        spec = dispatcher.importlib.util.spec_from_file_location("test_cloud_emulator", module_path)
        module = dispatcher.importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        payload = module.CloudEmulator(state_dir=cloud_dir).fetch_system_prompt()
    finally:
        if previous_project_root is None:
            dispatcher.os.environ.pop("BURNLESS_PROJECT_ROOT", None)
        else:
            dispatcher.os.environ["BURNLESS_PROJECT_ROOT"] = previous_project_root
        if previous_session_id is None:
            dispatcher.os.environ.pop("BURNLESS_SESSION_ID", None)
        else:
            dispatcher.os.environ["BURNLESS_SESSION_ID"] = previous_session_id

    assert cipher_decode(payload["ciphertext_block"], payload["key_hex"]) == decrypted


def test_worker_system_prompt_payload_exposes_cloud_emulator_session_env(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    role_dir = project_root / "_design" / "maestro_v1"
    role_dir.mkdir(parents=True)
    (role_dir / "glossary.md").write_text("glossary text", encoding="utf-8")
    (role_dir / "worker_role.md").write_text("worker role", encoding="utf-8")

    home_dir = tmp_path / "home"
    cloud_dir = home_dir / ".burnless"
    cloud_dir.mkdir(parents=True)
    monkeypatch.setattr(dispatcher.Path, "home", classmethod(lambda cls: home_dir))

    module_path = cloud_dir / "cloud_emulator.py"
    module_path.write_text(
        Path("/Users/roberto/.burnless/cloud_emulator.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    payload = dispatcher._worker_system_prompt_payload(project_root)

    assert payload["prompt"]
    assert payload["session_env"]["BURNLESS_SESSION_ID"]
    assert payload["session_env"]["BURNLESS_SESSION_KEY_HEX"]
    assert payload["session_env"]["BURNLESS_SESSION_CIPHERTEXT_B64"]
    assert payload["session_env"]["BURNLESS_SESSION_PLAINTEXT_WRAPPER"]
    assert (
        cipher_decode(
            payload["session_env"]["BURNLESS_SESSION_CIPHERTEXT_B64"],
            payload["session_env"]["BURNLESS_SESSION_KEY_HEX"],
        )
        == payload["prompt"]
    )


# ── _last_capsule: stream-json path ──────────────────────────────────────────

def test_last_capsule_stream_json():
    system_line = json.dumps({"type": "system", "subtype": "init"})
    result_line = json.dumps(
        {"type": "result", "result": "brz sum docs/x.md :: OK summarized [ref:exec/T0001]"}
    )
    stdout = system_line + "\n" + result_line
    cap = dispatcher._last_capsule(stdout)
    assert cap is not None
    assert cap.startswith("brz sum")
    assert "OK" in cap
    assert "T0001" in cap


def test_last_capsule_plain_text_fallback():
    stdout = "brz sum docs/y.md :: OK done [ref:exec/T0002]"
    cap = dispatcher._last_capsule(stdout)
    assert cap is not None
    assert cap.startswith("brz sum")
    assert "OK" in cap


def test_last_capsule_stream_json_backtick_wrapped():
    """Test that backtick-wrapped capsule headers are extracted correctly."""
    result_line = json.dumps(
        {"type": "result", "result": "`brz sum README.md :: OK` — Burnless is a multi-tier orchestration framework. MIT."}
    )
    stdout = '{"type":"system"}\n' + result_line
    cap = dispatcher._last_capsule(stdout)
    assert cap is not None, "backtick-wrapped capsule not extracted"
    assert dispatcher._capsule_status(cap) == "OK", f"status mismatch: {cap}, {dispatcher._capsule_status(cap)}"
    assert cap.startswith("brz sum")
    assert "README.md" in cap


def test_last_capsule_plain_text_backtick_wrapped():
    """Test backtick-wrapped capsule in plain-text fallback."""
    stdout = "`brz sum x.md :: OK` done [ref:exec/T1]"
    cap = dispatcher._last_capsule(stdout)
    assert cap is not None, "backtick-wrapped plain-text capsule not extracted"
    assert dispatcher._capsule_status(cap) == "OK"
    assert cap.startswith("brz sum")


# ── run_delegate: agents_mod.run integration ──────────────────────────────────

def test_run_delegate_extracts_stream_json_capsule(tmp_path, monkeypatch):
    project_root = tmp_path / "proj"
    burnless_root = project_root / ".burnless"
    (project_root / "_design" / "maestro_v1").mkdir(parents=True)
    (project_root / "_design" / "maestro_v1" / "worker_role.md").write_text(
        "worker role", encoding="utf-8"
    )
    burnless_root.mkdir(parents=True)

    config = {
        "agents": {
            "bronze": {
                "name": "bronze",
                "command": "/usr/bin/fake-agent -p",
                "provider": "claude",
            }
        }
    }
    spec = dispatcher.DelegateSpec(
        id=1,
        tier="bronze",
        action="sum",
        target="docs/x.md",
        spec="summarize",
        raw_line="del T1 bronze sum docs/x.md :: summarize",
    )

    result_line = json.dumps(
        {"type": "result", "result": "brz sum docs/x.md :: OK summarized [ref:exec/T0001]"}
    )
    fake_agent_result = {
        "stdout": '{"type":"system"}\n' + result_line,
        "stderr": "",
        "returncode": 0,
        "command": ["/usr/bin/fake-agent", "-p"],
        "timed_out": False,
        "interrupted": False,
    }

    monkeypatch.setattr(dispatcher.agents_mod, "run", lambda *a, **kw: fake_agent_result)
    monkeypatch.setattr(
        dispatcher.agents_mod, "resolve_command", lambda cfg: ["/usr/bin/fake-agent", "-p"]
    )
    monkeypatch.setattr(dispatcher.shutil, "which", lambda x: "/usr/bin/fake-agent")
    monkeypatch.setattr(dispatcher, "load_glossary", lambda root: "glossary")
    monkeypatch.setattr(
        dispatcher.Path, "home", classmethod(lambda cls: tmp_path / "no-home")
    )
    monkeypatch.setattr(
        dispatcher, "modulate_by_compression", lambda tier, kw, mode: (tier, "")
    )

    import burnless.plugin_loader as _plmod
    monkeypatch.setattr(_plmod, "load_plugins", lambda *a, **kw: [])
    monkeypatch.setattr(_plmod, "call_all_plugins", lambda *a, **kw: {})

    import burnless.prompt_context as _pc
    monkeypatch.setattr(_pc, "_with_runtime_context", lambda prompt, **kw: prompt)

    capsule_line = dispatcher.run_delegate(
        spec,
        burnless_root=burnless_root,
        project_root=project_root,
        config=config,
    )
    assert "OK" in capsule_line


# ── run_all / run_all_detailed ────────────────────────────────────────────────

def _make_env(tmp_path, monkeypatch):
    """Shared setup for run_all / run_all_detailed tests."""
    project_root = tmp_path / "proj"
    burnless_root = project_root / ".burnless"
    (project_root / "_design" / "maestro_v1").mkdir(parents=True)
    (project_root / "_design" / "maestro_v1" / "worker_role.md").write_text(
        "worker role", encoding="utf-8"
    )
    burnless_root.mkdir(parents=True)

    config = {
        "agents": {
            "bronze": {
                "name": "bronze",
                "command": "/usr/bin/fake-agent -p",
                "provider": "claude",
            }
        }
    }

    result_line = json.dumps(
        {"type": "result", "result": "brz sum docs/x.md :: OK summarized [ref:exec/T0001]",
         "usage": {"cache_read_input_tokens": 12345, "output_tokens": 50}}
    )
    fake_agent_result = {
        "stdout": '{"type":"system"}\n' + result_line,
        "stderr": "",
        "returncode": 0,
        "command": ["/usr/bin/fake-agent", "-p"],
        "timed_out": False,
        "interrupted": False,
        "usage": {"cache_read_input_tokens": 12345, "output_tokens": 50},
    }

    monkeypatch.setattr(dispatcher.agents_mod, "run", lambda *a, **kw: fake_agent_result)
    monkeypatch.setattr(
        dispatcher.agents_mod, "resolve_command", lambda cfg: ["/usr/bin/fake-agent", "-p"]
    )
    monkeypatch.setattr(dispatcher.shutil, "which", lambda x: "/usr/bin/fake-agent")
    monkeypatch.setattr(dispatcher, "load_glossary", lambda root: "glossary")
    monkeypatch.setattr(
        dispatcher.Path, "home", classmethod(lambda cls: tmp_path / "no-home")
    )
    monkeypatch.setattr(
        dispatcher, "modulate_by_compression", lambda tier, kw, mode: (tier, "")
    )

    import burnless.plugin_loader as _plmod
    monkeypatch.setattr(_plmod, "load_plugins", lambda *a, **kw: [])
    monkeypatch.setattr(_plmod, "call_all_plugins", lambda *a, **kw: {})

    import burnless.prompt_context as _pc
    monkeypatch.setattr(_pc, "_with_runtime_context", lambda prompt, **kw: prompt)

    return project_root, burnless_root, config


def test_run_all_returns_list_of_str(tmp_path, monkeypatch):
    project_root, burnless_root, config = _make_env(tmp_path, monkeypatch)
    delegate_lines = ["del T1 bronze sum docs/x.md :: summarize"]
    result = dispatcher.run_all(
        delegate_lines,
        burnless_root=burnless_root,
        project_root=project_root,
        config=config,
    )
    assert isinstance(result, list)
    assert all(isinstance(item, str) for item in result)
    assert "OK" in result[0]


def test_run_all_detailed_returns_dicts_with_usage(tmp_path, monkeypatch):
    project_root, burnless_root, config = _make_env(tmp_path, monkeypatch)
    delegate_lines = ["del T1 bronze sum docs/x.md :: summarize"]
    details = dispatcher.run_all_detailed(
        delegate_lines,
        burnless_root=burnless_root,
        project_root=project_root,
        config=config,
    )
    assert isinstance(details, list)
    assert len(details) == 1
    d = details[0]
    assert "capsule" in d
    assert "usage" in d
    assert "status" in d
    assert d["usage"].get("cache_read_input_tokens") == 12345
