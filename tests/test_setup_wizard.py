from pathlib import Path

from burnless import paths, setup_wizard, state


class _Tty:
    def isatty(self):
        return True


def test_suggest_prefers_codex_and_sets_model_reasoning_by_tier():
    det = setup_wizard.Detection(
        clis={
            "codex": setup_wizard.CliInfo(
                name="codex",
                path="/usr/local/bin/codex",
                models=["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"],
            ),
            "claude": setup_wizard.CliInfo(name="claude", path="/usr/local/bin/claude"),
        }
    )

    rec = setup_wizard.suggest(det)

    assert rec["gold"]["name"] == "codex-gpt-5.5-medium"
    assert "--model gpt-5.5" in rec["gold"]["command"]
    assert "model_reasoning_effort=medium" in rec["gold"]["command"]
    assert "--sandbox workspace-write" in rec["gold"]["command"]

    assert rec["silver"]["name"] == "codex-gpt-5.5-low"
    assert "model_reasoning_effort=low" in rec["silver"]["command"]
    assert "--sandbox workspace-write" in rec["silver"]["command"]

    assert rec["bronze"]["name"] == "codex-gpt-5.4-mini-low"
    assert "--model gpt-5.4-mini" in rec["bronze"]["command"]
    assert "model_reasoning_effort=low" in rec["bronze"]["command"]
    assert "--sandbox read-only" in rec["bronze"]["command"]


def test_run_non_interactive_defaults_shell_tier_to_auto(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        setup_wizard,
        "detect",
        lambda: setup_wizard.Detection(
            clis={
                "codex": setup_wizard.CliInfo(
                    name="codex",
                    path="/usr/local/bin/codex",
                    models=["gpt-5.5", "gpt-5.4-mini"],
                )
            }
        ),
    )

    assert setup_wizard.run(non_interactive=True, project="demo") == 0

    p = paths.paths_for(tmp_path / ".burnless")
    saved_state = state.load(p["state"])
    output = capsys.readouterr().out

    assert saved_state["active_tier"] is None
    assert "Recommended tier mapping:" in output
    assert "default shell tier: auto" in output
    assert "codex-gpt-5.5-medium" in output


def test_run_interactive_persists_chosen_default_shell_tier(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        setup_wizard,
        "detect",
        lambda: setup_wizard.Detection(
            clis={
                "codex": setup_wizard.CliInfo(
                    name="codex",
                    path="/usr/local/bin/codex",
                    models=["gpt-5.5", "gpt-5.4-mini"],
                )
            }
        ),
    )
    monkeypatch.setattr(setup_wizard, "_confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr("builtins.input", lambda prompt: "silver")

    assert setup_wizard.run(project="demo") == 0

    p = paths.paths_for(tmp_path / ".burnless")
    saved_state = state.load(p["state"])
    output = capsys.readouterr().out

    assert saved_state["active_tier"] == "silver"
    assert "default shell tier: silver" in output


def test_run_non_interactive_preserves_existing_shell_tier(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        setup_wizard,
        "detect",
        lambda: setup_wizard.Detection(
            clis={
                "codex": setup_wizard.CliInfo(
                    name="codex",
                    path="/usr/local/bin/codex",
                    models=["gpt-5.5", "gpt-5.4-mini"],
                )
            }
        ),
    )

    assert setup_wizard.run(non_interactive=True, project="demo") == 0
    p = paths.paths_for(tmp_path / ".burnless")
    saved_state = state.load(p["state"])
    saved_state["active_tier"] = "silver"
    state.save(p["state"], saved_state)

    assert setup_wizard.run(non_interactive=True, project="demo") == 0

    saved_state = state.load(p["state"])
    output = capsys.readouterr().out

    assert saved_state["active_tier"] == "silver"
    assert "default shell tier: silver" in output
