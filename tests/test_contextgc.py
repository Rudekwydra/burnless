import json
from pathlib import Path

from burnless.contextgc import load_transcript, collapse, expand, measure


def _write_transcript(tmp_path: Path) -> Path:
    path = tmp_path / "transcript.jsonl"
    rows = [
        {"message": {"role": "user", "content": "turn 1: pedir X"}},
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Read",
                        "input": {"file_path": "/repo/a.py", "query": "x" * 300},
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": "result " + ("A" * 600),
                    },
                    {
                        "type": "tool_use",
                        "id": "tool-2",
                        "name": "Write",
                        "input": {"file_path": "/repo/b.py", "content": "B" * 400},
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-2",
                        "content": "result " + ("C" * 500),
                    },
                ],
            }
        },
        {"message": {"role": "user", "content": "turn 2: pedir Y"}},
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-3",
                        "name": "Edit",
                        "input": {"file_path": "/repo/c.py", "content": "small"},
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-3",
                        "content": "ok",
                    },
                ],
            }
        },
        {"message": {"role": "user", "content": "turn 3: pedir Z"}},
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-4",
                        "name": "Read",
                        "input": {"file_path": "/repo/d.py", "query": "late"},
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-4",
                        "content": "ok",
                    },
                ],
            }
        },
        {"message": {"role": "user", "content": "turn 4: fechar"}},
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-5",
                        "name": "Final",
                        "input": {"content": "final"},
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-5",
                        "content": "done",
                    },
                ],
            }
        },
        {"message": {"role": "user", "content": "turn 5: finalizar"}},
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-6",
                        "content": "wrap-up",
                    }
                ],
            }
        },
    ]
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def test_fidelity(tmp_path):
    """Verifica fidelidade 100% na re-expansão de ponteiros."""
    transcript = _write_transcript(tmp_path)
    collapsed_events, index = collapse(str(transcript))

    pointers = []
    for event in collapsed_events:
        if isinstance(event["content"], list):
            for block in event["content"]:
                if isinstance(block, dict) and "ptr" in block:
                    pointers.append(block)

    original_events = load_transcript(str(transcript))
    assert pointers, "nenhum ponteiro gerado"
    for pointer in pointers:
        ref_id = pointer["ptr"]
        expanded = expand(ref_id, index)

        original_event = original_events[pointer["line"]]
        original_block = original_event["content"][pointer["block"]]
        assert expanded == original_block, f"Fidelity mismatch para {ref_id}"


def test_reduction(tmp_path):
    """Verifica que reduction >= 40%."""
    transcript = _write_transcript(tmp_path)
    result = measure(str(transcript))
    reduction = result["reduction_pct"]
    assert reduction >= 40.0, f"Redução {reduction}% < 40%"


def test_hash_drift(tmp_path):
    """Ponteiro carrega hash; expand detecta drift do source."""
    transcript = _write_transcript(tmp_path)
    collapsed_events, index = collapse(str(transcript))
    pointers = [b for e in collapsed_events if isinstance(e["content"], list)
                for b in e["content"] if isinstance(b, dict) and "ptr" in b]
    assert pointers, "nenhum ponteiro gerado"
    for p in pointers:
        assert len(p.get("hash", "")) == 16, f"ponteiro {p['ptr']} sem hash"
    rid = pointers[0]["ptr"]
    expand(rid, index)
    index[rid]["hash"] = "deadbeefdeadbeef"
    try:
        expand(rid, index)
        raise AssertionError("drift não detectado")
    except ValueError as e:
        assert "mismatch" in str(e)
