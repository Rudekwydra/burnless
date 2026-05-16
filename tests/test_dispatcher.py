from __future__ import annotations

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
