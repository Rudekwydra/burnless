import pytest
import tempfile
import json
from pathlib import Path

from burnless.epochs_v2 import (
    extract_entities,
    is_noop,
    parse_living,
    harvest_state,
    living_rewrite_prompt,
    preserve_guard,
    enforce_budget,
    apply_capture,
    push_ring,
    living_path,
    state_path,
    ring_dir,
    living_seed,
    SECTIONS,
)


def test_extract_entities_paths():
    text = "arquivo em /Users/roberto/antigravity/test.py"
    entities = extract_entities(text)
    assert "/Users/roberto/antigravity/test.py" in entities


def test_extract_entities_delegation_id():
    text = "veja d702 ou d123 no burnless"
    entities = extract_entities(text)
    assert "d702" in entities
    assert "d123" in entities


def test_extract_entities_commit_hash():
    text = "commit abc1234567890def e5c0def123456789"
    entities = extract_entities(text)
    assert "abc1234567890def" in entities or any(h for h in entities if "abc1234" in h)
    assert "e5c0def123456789" in entities or any(h for h in entities if "e5c0def" in h)


def test_extract_entities_file_ext():
    text = "abra epochs_v2.py e schema.json"
    entities = extract_entities(text)
    assert "epochs_v2.py" in entities
    assert "schema.json" in entities


def test_extract_entities_empty():
    assert extract_entities("") == set()
    assert extract_entities("    ") == set()


def test_is_noop_short_no_new_entity():
    prev_md = "tem /a/b.py e d702 aqui"
    exchange = "ok manda"
    assert is_noop(prev_md, exchange) is True


def test_is_noop_new_entity_false():
    prev_md = "tem /a/b.py aqui"
    exchange = "vai em /new/path.ts"
    assert is_noop(prev_md, exchange) is False


def test_is_noop_long_exchange():
    prev_md = "x"
    exchange = "a" * 300
    assert is_noop(prev_md, exchange) is False


def test_is_noop_empty_exchange():
    prev_md = "algo"
    exchange = ""
    assert is_noop(prev_md, exchange) is True


def test_parse_living_5_sections():
    md = """## Foco atual
- linha 1
- linha 2

## Threads abertas
- thread a
- thread b

## Decisões
- dec 1

## Contracts
- a.py:1 foo()

## Refs
- /path/to/file
"""
    parsed = parse_living(md)
    assert "Foco atual" in parsed
    assert "linha 1" in parsed["Foco atual"] or "- linha 1" in parsed["Foco atual"]
    assert "thread a" in parsed["Threads abertas"] or "- thread a" in parsed["Threads abertas"]
    assert "dec 1" in parsed["Decisões"] or "- dec 1" in parsed["Decisões"]
    assert "a.py:1 foo()" in parsed["Contracts"] or "- a.py:1 foo()" in parsed["Contracts"]


def test_parse_living_missing_sections():
    md = "## Foco atual\n- x\n"
    parsed = parse_living(md)
    assert parsed["Foco atual"] == ["x"] or parsed["Foco atual"] == ["- x"]
    assert parsed["Threads abertas"] == []
    assert parsed["Decisões"] == []


def test_harvest_state():
    md = """## Foco atual
- current

## Threads abertas
- pending task

## Decisões
- decided yes

## Contracts
- a.py:1 foo()

## Refs
- d702
- /some/path
"""
    harvested = harvest_state(md)
    assert harvested["contracts"] == ["a.py:1 foo()"]
    assert "pending task" in harvested["open_threads"]
    assert "d702" in harvested["refs"]


def test_living_rewrite_prompt_includes_sections():
    prev = "## Foco atual\n- x\n"
    exchange = "new thing"
    prompt = living_rewrite_prompt(prev, exchange, 2500)
    assert "## Foco atual" in prompt
    assert "## Threads abertas" in prompt
    assert "## Decisões" in prompt
    assert "## Contracts" in prompt
    assert "## Refs" in prompt
    assert prev in prompt
    assert exchange in prompt


def test_preserve_guard_reappends_dropped_contract():
    prev_md = "## Contracts\n- a.py:1 foo()\n"
    new_md = "## Foco atual\n- updated\n"
    result = preserve_guard(prev_md, new_md)
    assert "a.py:1 foo()" in result
    assert "## Contracts" in result


def test_preserve_guard_keeps_present_contracts():
    prev_md = "## Contracts\n- a.py:1 foo()\n"
    new_md = "## Foco atual\n- x\n## Contracts\n- a.py:1 foo()\n"
    result = preserve_guard(prev_md, new_md)
    count = result.count("a.py:1 foo()")
    assert count == 1


def test_enforce_budget_trims_decisoes():
    md = """## Foco atual
- focus

## Threads abertas
- thread

## Decisões
- dec1
- dec2
- dec3
- dec4

## Contracts
- a.py:1

## Refs
- /path
"""
    result = enforce_budget(md, budget_tokens=20)
    parsed = parse_living(result)
    assert len(parsed["Decisões"]) < 4
    assert "a.py:1" in result


def test_enforce_budget_under_limit():
    md = "## Foco atual\n- x\n"
    result = enforce_budget(md, budget_tokens=10000)
    assert result == md


def test_apply_capture_with_stub_rewriter(tmp_path):
    stub_rewriter = lambda prompt: "## Foco atual\n- x\n## Threads abertas\n## Decisões\n## Contracts\n- a.py:1 foo()\n## Refs\n"

    lp = apply_capture(tmp_path, "c1", "vai em /x/y.py", rewriter=stub_rewriter)
    assert lp.exists()
    assert lp.read_text().count("a.py:1 foo()") == 1

    sp = state_path(tmp_path, "c1")
    assert sp.exists()
    state_data = json.loads(sp.read_text())
    assert "contracts" in state_data


def test_apply_capture_noop_leaves_unchanged(tmp_path):
    stub_rewriter = lambda prompt: "## Foco atual\n- first\n## Threads abertas\n## Decisões\n## Contracts\n## Refs\n"

    lp = apply_capture(tmp_path, "c2", "short", rewriter=stub_rewriter)
    first_content = lp.read_text()

    apply_capture(tmp_path, "c2", "ok", rewriter=stub_rewriter)
    second_content = lp.read_text()

    assert first_content == second_content


def test_push_ring_keeps_10_max(tmp_path):
    for i in range(12):
        push_ring(tmp_path, "c3", f"exchange {i}")

    rd = ring_dir(tmp_path, "c3")
    files = list(rd.glob("*.md"))
    assert len(files) == 10


def test_living_seed_empty(tmp_path):
    seed = living_seed(tmp_path, "nonexistent")
    assert seed == ""


def test_living_seed_existing(tmp_path):
    lp = living_path(tmp_path, "c4")
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text("## Foco atual\n- seed")
    seed = living_seed(tmp_path, "c4")
    assert "seed" in seed


def test_apply_capture_fail_open(tmp_path):
    failing_rewriter = lambda prompt: None
    lp = apply_capture(tmp_path, "c5", "exchange", rewriter=failing_rewriter)
    assert lp.exists()


def test_extract_entities_case_preserved():
    text = "veja MyClass.java e UPPER.ext"
    entities = extract_entities(text)
    assert "MyClass.java" in entities
    assert "UPPER.ext" in entities


def test_parse_living_strips_dashes():
    md = "## Contracts\n- a.py:1 func()\n- b.ts:2 x()\n"
    parsed = parse_living(md)
    contracts = parsed["Contracts"]
    assert "a.py:1 func()" in contracts or "- a.py:1 func()" in contracts
    assert "b.ts:2 x()" in contracts or "- b.ts:2 x()" in contracts
