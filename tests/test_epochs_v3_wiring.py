from burnless import config
from burnless.epochs_v2 import apply_capture, _epochs_version

V3_DOC = (
    "## Foco atual\n- wire v3\n"
    "## Threads abertas\n- t1\n"
    "## Decisões\n- d1\n"
    "## Contracts\n- /x/y.py:1 foo()\n"
    "## Refs\n- ref1\n"
    "## Riscos\n- r1\n"
    "## Última validação\n- pytest -q OK\n"
    "## Recuperáveis\n- d729 — pytest tests/test_epochs_v3_wiring.py\n"
)

V2_DOC = (
    "## Foco atual\n- focus\n"
    "## Threads abertas\n- t1\n"
    "## Decisões\n- d1\n"
    "## Contracts\n- /x/y.py:1 foo()\n"
    "## Refs\n- ref1\n"
)


def test_apply_capture_version3_writes_v3_sections(tmp_path):
    rewriter = lambda prompt: V3_DOC
    lp = apply_capture(tmp_path, "v3", "trabalho em /x/y.py d729", rewriter=rewriter, version=3)
    content = lp.read_text()
    assert "## Riscos" in content
    assert "## Última validação" in content
    assert "## Recuperáveis" in content


def test_apply_capture_version2_preserves_v2(tmp_path):
    rewriter = lambda prompt: V2_DOC
    lp = apply_capture(tmp_path, "v2", "trabalho em /x/y.py d729", rewriter=rewriter, version=2)
    content = lp.read_text()
    assert "## Riscos" not in content
    assert "## Última validação" not in content
    assert "## Recuperáveis" not in content


def test_epochs_version_defaults_to_2(tmp_path):
    assert _epochs_version(tmp_path) == 2


def test_config_defaults_epochs_block():
    ep = config.DEFAULT_CONFIG["epochs"]
    assert ep["version"] == 3
    assert ep["budget_tokens"] == 2500
    assert ep["contract_max_age_turns"] == 15
    assert ep["recoverables_max_items"] == 12


def test_session_template_has_burnless_restore():
    path = "/Users/roberto/antigravity/burnless/templates/scripts/burnless_epoch_session.sh"
    with open(path, encoding="utf-8") as f:
        assert "Burnless restore" in f.read()
