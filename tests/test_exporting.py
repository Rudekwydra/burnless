import os
import time

import pytest
import yaml

from burnless import cli, exporting, recovery


# Real living_md V3 payload (sanitized from a production checkpoint of this
# repo) — full 8-section document with grammar lines, [chat:*] anchors and
# Recuperáveis pointers. Real payload over synthetic per worker protocol.
REAL_LIVING_MD = """Documento vivo V3 (Atualização)

## Foco atual
[state] Agent "Check journal record schema for timestamp field" finished with result: "Sim, tem timestamp. Campo `captured_at` (formato `%Y-%m-%dT%H:%M:%SZ`, UTC)." [chat:a4e17c3f4f1eb881a].
[state] Definição do dict: `extract_exchange` monta o envelope em `recovery.py:413-428` (inclui `"captured_at": time.strftime(...)` na linha 427). [chat:a4e17c3f4f1eb881a].
[state] Aguardando `b42a2r2s2` (F5) terminar [chat:29].

## Threads abertas
[inflight] Aguardando `b42a2r2s2` (F5) terminar [chat:29].

## Decisões
[state] Fable entregou plano L0/L1/L2 + Refs estruturado + epoch→cápsula [chat:14].
[state] Fix iso-cwd no `living_rewriter` auditado e commitado [chat:16].
[state] `captured_at` (UTC) é a idade canônica no journal [chat:a4e17c3f4f1eb881a].
[state] F4 auditado, testado (79 passed) e commitado [chat:28].

## Contracts
[state] exchange_id: sha256:24ed42d17a46fd0901bdb740dcbd976356f56380aef3ca7473567ce5e4f3b3d0
[state] exchange_id: sha256:e23cf35e0386e903c563e74765170d7fa465eaf63cf96f6fa12cf03b76e4b000

## Refs
recovery.py#L413-428 — envelope do extract_exchange [seq 12]

## Riscos
(Empty)

## Última validação
[state] Agent "Check journal record schema for timestamp field" finished [chat:29].

## Recuperáveis
d807 — arquitetura L0/L1/L2 + Refs estruturado + epoch→cápsula, plano faseado F1-F6 [chat:14]
d808 — fix iso-cwd no `living_rewriter` [chat:16]
a4e17c3f4f1eb881a — Agent "Check journal record schema for timestamp field" finished [chat:a4e17c3f4f1eb881a]
"""

SID = "abcdef12-3456-7890-abcd-ef1234567890"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "demo" / ".burnless"
    (root / "epochs").mkdir(parents=True)
    return tmp_path / "demo"


def _checkpoint(project_root, living_md, *, sid=SID, applied_through=10, journal_head=12):
    return recovery.write_checkpoint(
        project_root,
        host="claude",
        host_session_id=sid,
        process_instance_id="proc-1",
        living_md=living_md,
        harvested_state={},
        applied_through=applied_through,
        journal_head=journal_head,
    )


def _parse_export(path):
    """Split front-matter from body; body must be the living_md verbatim."""
    raw = path.read_text(encoding="utf-8")
    assert raw.startswith("---\n")
    head, body = raw[4:].split("\n---\n", 1)
    return yaml.safe_load(head), body


def test_export_writes_front_matter_and_verbatim_living_md(project):
    # Explicit en_markers: false to ensure PT markers are preserved verbatim
    burnless_dir = project / ".burnless"
    config_file = burnless_dir / "config.yaml"
    config_file.write_text("format:\n  en_markers: false\n", encoding="utf-8")
    payload = _checkpoint(project, REAL_LIVING_MD)

    result = exporting.export_epoch(project, host="claude", host_session_id=SID)
    assert result["status"] == "exported"

    exports = list((project / ".burnless" / "exports").glob("epoch-*.md"))
    assert len(exports) == 1
    name = exports[0].name
    assert name.startswith(f"epoch-claude-{SID[:8]}-")
    assert name.endswith(".md")

    meta, body = _parse_export(exports[0])
    assert meta["schema"] == "burnless-epoch-export/v1"
    assert meta["project"] == "demo"
    assert meta["host"] == "claude"
    assert meta["host_session_id"] == SID
    assert meta["generation"] == payload["generation"]
    assert meta["applied_through"] == 10
    assert meta["journal_head"] == 12
    assert meta["created"]  # ISO8601 UTC, parseable by yaml
    # living_md must survive byte-identical
    assert body.encode("utf-8") == REAL_LIVING_MD.encode("utf-8")


def test_export_empty_living_md_writes_nothing(project):
    _checkpoint(project, "")

    result = exporting.export_epoch(project, host="claude", host_session_id=SID)
    assert result["status"] == "export_skipped"
    assert result["reason"] == "empty_living_md"
    exports_dir = project / ".burnless" / "exports"
    assert not exports_dir.exists() or not list(exports_dir.glob("*.md"))


def test_export_retention_gc_removes_oldest(project):
    (project / ".burnless" / "config.yaml").write_text(
        "epochs:\n  exports_keep: 2\n", encoding="utf-8"
    )
    sids = [f"{i}{SID[1:]}" for i in range(3)]
    for i, sid in enumerate(sids):
        _checkpoint(project, REAL_LIVING_MD, sid=sid)
        result = exporting.export_epoch(project, host="claude", host_session_id=sid)
        assert result["status"] == "exported"
        # force distinct, ordered mtimes (exports can land in the same second)
        path = project / ".burnless" / "exports" / os.path.basename(result["path"])
        stamp = time.time() - 100 + i
        os.utime(path, (stamp, stamp))
        exporting._gc_exports(path.parent, 2)

    remaining = sorted(
        p.name for p in (project / ".burnless" / "exports").glob("epoch-*.md")
    )
    assert len(remaining) == 2
    assert not any(sids[0][:8] in name for name in remaining), remaining
    assert any(sids[1][:8] in name for name in remaining)
    assert any(sids[2][:8] in name for name in remaining)


def test_export_fail_open_never_raises(project, monkeypatch):
    _checkpoint(project, REAL_LIVING_MD)

    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(recovery, "read_checkpoint", boom)
    result = exporting.export_epoch(project, host="claude", host_session_id=SID)
    assert result["status"] == "export_skipped"
    assert "disk on fire" in result["reason"]


def test_cli_epoch_export_wired(project, capsys):
    _checkpoint(project, REAL_LIVING_MD)

    rc = cli.main(
        ["epoch", "export", "--root", str(project / ".burnless"),
         "--host", "claude", "--host-session-id", SID]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert '"status": "exported"' in out
    assert list((project / ".burnless" / "exports").glob("epoch-*.md"))


def test_cli_epoch_seal_removed():
    with pytest.raises(SystemExit):
        cli.main(["epoch", "seal", "--root", "/tmp", "--host-session-id", SID])
