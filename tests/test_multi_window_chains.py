from __future__ import annotations

import threading

from burnless import recovery


def _seed_window(root, *, pid: str, sid: str, marker: str, cwd: str) -> None:
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id=sid,
        process_instance_id=pid,
        living_md=f"## Foco atual\n- {marker}\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )
    recovery.journal_append(
        root,
        {
            "host": "claude",
            "host_session_id": sid,
            "process_instance_id": pid,
            "exchange_id": f"sha256:{sid}-1",
            "user_text": f"pergunta {marker}",
            "assistant_text": f"resposta {marker}",
            "files": [],
            "cwd": cwd,
        },
    )
    recovery.write_handoff(root, host="claude", host_session_id=sid, process_instance_id=pid, cwd=cwd)


def test_three_windows_interleaved_no_cross_contamination(tmp_path):
    root = tmp_path / ".burnless"
    windows = [
        {"pid": "host-11111", "sid": "sid-a", "marker": "FOCO_JANELA_A_TAREFA_X", "cwd": "/tmp/proj-a"},
        {"pid": "host-22222", "sid": "sid-b", "marker": "FOCO_JANELA_B_TAREFA_Y", "cwd": "/tmp/proj-b"},
        {"pid": "host-33333", "sid": "sid-c", "marker": "FOCO_JANELA_C_TAREFA_Z", "cwd": "/tmp/proj-c"},
    ]
    # escreve intercalado: um turno de cada janela por vez, nunca todos de uma
    # janela seguidos, pra simular 3 terminais reais alternando turnos.
    for w in windows:
        _seed_window(root, pid=w["pid"], sid=w["sid"], marker=w["marker"], cwd=w["cwd"])

    for w in windows:
        new_sid = f"{w['sid']}-clear"
        claimed = recovery.claim_handoff(
            root, host="claude", process_instance_id=w["pid"], new_session_id=new_sid, cwd=w["cwd"]
        )
        assert claimed is not None, f"window {w['sid']} lost its own handoff"
        assert claimed["claim_mode"] == "pid"
        assert claimed["host_session_id"] == w["sid"]

        payload = recovery.render_restore(
            root,
            host="claude",
            host_session_id=w["sid"],
            process_instance_id=w["pid"],
            new_session_id=new_sid,
            source="clear",
        )
        assert payload is not None
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert w["marker"] in ctx, f"{w['sid']}: must_remember lost"
        for other in windows:
            if other is w:
                continue
            assert other["marker"] not in ctx, f"{w['sid']}: leaked {other['sid']}'s content (must_forget)"


def test_simultaneous_clear_do_not_corrupt_each_other(tmp_path):
    """/clear real e concorrente em 5 janelas (threads de verdade, não
    sequencial) -- prova que o lock exclusivo do claim_handoff impede
    corrupção/roubo cruzado sob concorrência real."""
    root = tmp_path / ".burnless"
    windows = [
        {"pid": f"host-{40000 + i}", "sid": f"sid-conc-{i}", "marker": f"MARCADOR_CONCORRENTE_{i}", "cwd": f"/tmp/proj-conc-{i}"}
        for i in range(5)
    ]
    for w in windows:
        _seed_window(root, pid=w["pid"], sid=w["sid"], marker=w["marker"], cwd=w["cwd"])

    results: dict[int, dict] = {}
    errors: list[Exception] = []

    def _claim(index: int, w: dict) -> None:
        try:
            claimed = recovery.claim_handoff(
                root,
                host="claude",
                process_instance_id=w["pid"],
                new_session_id=f"{w['sid']}-clear",
                cwd=w["cwd"],
            )
            results[index] = claimed
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_claim, args=(i, w)) for i, w in enumerate(windows)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"claim_handoff raised under concurrency: {errors}"
    assert len(results) == len(windows)
    for i, w in enumerate(windows):
        claimed = results.get(i)
        assert claimed is not None, f"window {i} lost its handoff under concurrency"
        assert claimed["claim_mode"] == "pid"
        assert claimed["host_session_id"] == w["sid"], f"window {i} got the wrong lineage"


def test_ten_windows_same_root_different_tasks(tmp_path):
    """Cenario real do Roberto: 10 janelas no mesmo projeto, cada uma numa
    tarefa diferente -- todas coexistem sem contaminação e list_chains
    reporta as 10 distintas."""
    root = tmp_path / ".burnless"
    windows = [
        {"pid": f"host-{90000 + i}", "sid": f"sid-ten-{i}", "marker": f"TAREFA_JANELA_{i}", "cwd": f"/tmp/proj-ten-{i}"}
        for i in range(10)
    ]
    for w in windows:
        _seed_window(root, pid=w["pid"], sid=w["sid"], marker=w["marker"], cwd=w["cwd"])

    chains = recovery.list_chains(root, host="claude")
    assert len(chains) == 10

    for w in windows:
        new_sid = f"{w['sid']}-clear"
        claimed = recovery.claim_handoff(
            root, host="claude", process_instance_id=w["pid"], new_session_id=new_sid, cwd=w["cwd"]
        )
        assert claimed is not None
        assert claimed["host_session_id"] == w["sid"]
        payload = recovery.render_restore(
            root,
            host="claude",
            host_session_id=w["sid"],
            process_instance_id=w["pid"],
            new_session_id=new_sid,
            source="clear",
        )
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert w["marker"] in ctx
        others = [o["marker"] for o in windows if o is not w]
        assert not any(o in ctx for o in others), f"{w['sid']}: leaked another window's marker"
