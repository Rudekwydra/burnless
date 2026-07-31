"""Spine proxy: transform, store, and compressor contracts.

The invariants under test are the ones the design stands on
(_design/SPINE_PROXY_2026-07-31.md): turn-1 passthrough, fail-open on any
surprise, prefix absorption in order, append-only byte-stable spine, tail
kept verbatim, capsules frozen (first write wins), and substitution only
when it actually shrinks the request.
"""
from __future__ import annotations

import json

import pytest

from burnless.proxy import spine
from burnless.proxy.compressor import CompressorThread, compress_exchange
from burnless.proxy.store import CapsuleStore


def user(text: str) -> dict:
    return {"role": "user", "content": text}


def assistant(text: str) -> dict:
    return {"role": "assistant", "content": text}


def convo(n_exchanges: int, *, tail_prompt: str = "next question", mult: int = 40) -> list:
    msgs: list = []
    for i in range(n_exchanges):
        msgs.append(user(f"question {i}: " + ("lorem ipsum " * mult)))
        msgs.append(assistant(f"answer {i}: " + ("dolor sit amet " * mult)))
    msgs.append(user(tail_prompt))
    return msgs


class MemStore:
    def __init__(self):
        self.capsules: dict[str, str] = {}
        self.originals: dict[str, list] = {}

    def get_capsule(self, h):
        return self.capsules.get(h)

    def put_capsule(self, h, text):
        self.capsules.setdefault(h, text)

    def put_original(self, h, ex):
        self.originals.setdefault(h, ex)

    def get_original(self, h):
        return self.originals.get(h)


def fill_capsules(store: MemStore, messages: list) -> None:
    completed, _ = spine.split_exchanges(messages)
    for ex in completed:
        h = spine.exchange_hash(ex)
        cap = compress_exchange(ex, h)
        assert cap is not None
        store.put_capsule(h, cap)


# ---------------------------------------------------------------- split


def test_split_exchanges_basic():
    msgs = convo(2)
    completed, tail = spine.split_exchanges(msgs)
    assert len(completed) == 2
    assert tail == [msgs[-1]]
    assert all(ex[0]["role"] == "user" for ex in completed)


def test_split_tool_result_user_message_stays_inside_exchange():
    msgs = [
        user("do the thing"),
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
        assistant("done"),
        user("thanks, next"),
    ]
    completed, tail = spine.split_exchanges(msgs)
    assert len(completed) == 1
    assert len(completed[0]) == 4
    assert tail == [msgs[-1]]


def test_split_fails_open_on_weird_shapes():
    assert spine.split_exchanges([assistant("orphan first")]) == ([], [assistant("orphan first")])
    assert spine.split_exchanges([]) == ([], [])


# ---------------------------------------------------------------- transform


def test_turn_one_is_pure_passthrough():
    store = MemStore()
    body = {"model": "m", "messages": [user("hello world")]}
    new_body, pending, stats = spine.transform(body, store)
    assert new_body is body
    assert not stats["applied"]
    assert pending == []


def test_below_regime_passthrough_but_precomputes_pending():
    store = MemStore()
    body = {"model": "m", "messages": convo(2)}
    new_body, pending, stats = spine.transform(body, store, min_exchanges=3)
    assert not stats["applied"]
    # exchange 0 left the tail (keep_tail=1) → original persisted, queued
    assert len(pending) == 1
    assert pending[0] in store.originals


def test_substitution_with_capsules_present():
    store = MemStore()
    msgs = convo(4)
    fill_capsules(store, msgs)
    body = {"model": "m", "messages": msgs, "system": "SYSTEM", "max_tokens": 5}
    new_body, pending, stats = spine.transform(body, store)
    assert stats["applied"] and stats["absorbed"] == 3
    assert pending == []
    out = new_body["messages"]
    # spine user + ack + last completed exchange verbatim (2 msgs) + tail (1)
    assert len(out) == 5
    blocks = out[0]["content"]
    assert out[0]["role"] == "user" and isinstance(blocks, list)
    assert blocks[0]["text"] == spine.SPINE_HEADER
    assert len(blocks) == 4  # header + one block per absorbed capsule
    assert out[1] == {"role": "assistant", "content": spine.SPINE_ACK}
    assert out[2:] == msgs[6:]  # tail region untouched, byte-identical
    # untouched request fields survive
    assert new_body["system"] == "SYSTEM" and new_body["max_tokens"] == 5
    # it actually shrank
    assert len(json.dumps(new_body)) < len(json.dumps(body))
    # every absorbed capsule carries its ref, one block each
    for ex, block in zip(spine.split_exchanges(msgs)[0][:3], blocks[1:]):
        assert f"[ref:{spine.exchange_hash(ex)}]" in block["text"]


def test_spine_prefix_is_byte_stable_across_turns():
    store = MemStore()
    msgs_n = convo(4)
    # turn N+1 = same history + the tail answered + a new prompt
    msgs_n1 = msgs_n + [assistant("tail answer: " + "x " * 40), user("new prompt")]
    fill_capsules(store, msgs_n1)
    body_n, _, stats_n = spine.transform({"model": "m", "messages": msgs_n}, store)
    body_n1, _, stats_n1 = spine.transform({"model": "m", "messages": msgs_n1}, store)
    assert stats_n["applied"] and stats_n1["applied"]

    def content_blocks(body):
        return [
            {k: v for k, v in b.items() if k != "cache_control"}
            for b in body["messages"][0]["content"]
        ]

    blocks_n, blocks_n1 = content_blocks(body_n), content_blocks(body_n1)
    # append-only: turn-N blocks are a strict, byte-identical prefix of turn N+1
    assert len(blocks_n1) == len(blocks_n) + 1
    assert blocks_n1[: len(blocks_n)] == blocks_n


def test_cache_breakpoint_rides_the_last_capsule_block():
    store = MemStore()
    msgs = convo(4)
    fill_capsules(store, msgs)
    new_body, _, stats = spine.transform({"model": "m", "messages": msgs}, store)
    blocks = new_body["messages"][0]["content"]
    assert stats["cache_breakpoint"] is True
    assert blocks[-1].get("cache_control") == {"type": "ephemeral"}
    assert all("cache_control" not in b for b in blocks[:-1])


def test_cache_breakpoint_respects_client_budget_of_four():
    store = MemStore()
    msgs = convo(4)
    fill_capsules(store, msgs)
    body = {
        "model": "m",
        "messages": msgs,
        "system": [
            {"type": "text", "text": f"s{i}", "cache_control": {"type": "ephemeral"}} for i in range(4)
        ],
    }
    new_body, _, stats = spine.transform(body, store)
    assert stats["applied"] and stats["cache_breakpoint"] is False
    assert all("cache_control" not in b for b in new_body["messages"][0]["content"])


def test_cache_breakpoint_can_be_disabled():
    store = MemStore()
    msgs = convo(4)
    fill_capsules(store, msgs)
    new_body, _, stats = spine.transform({"model": "m", "messages": msgs}, store, cache_breakpoint=False)
    assert stats["applied"] and stats["cache_breakpoint"] is False
    assert all("cache_control" not in b for b in new_body["messages"][0]["content"])


def test_missing_middle_capsule_stops_absorption_preserving_order():
    store = MemStore()
    # big exchanges so absorbing even a single one beats header + overhead
    msgs = convo(5, mult=150)
    completed, _ = spine.split_exchanges(msgs)
    for i, ex in enumerate(completed[:-1]):
        if i == 1:
            continue  # capsule for exchange 1 not ready yet
        h = spine.exchange_hash(ex)
        store.put_capsule(h, compress_exchange(ex, h))
    new_body, pending, stats = spine.transform({"model": "m", "messages": msgs}, store)
    assert stats["applied"] and stats["absorbed"] == 1
    out = new_body["messages"]
    # exchange 1 onward stays verbatim, in order, right after the ack
    assert out[2:] == msgs[2:]
    assert pending == [spine.exchange_hash(completed[1])]


def test_no_capsules_yet_is_passthrough():
    store = MemStore()
    body = {"model": "m", "messages": convo(4)}
    new_body, pending, stats = spine.transform(body, store)
    assert not stats["applied"] and stats["reason"] == "no_capsules_yet"
    assert new_body is body
    assert len(pending) == 3


def test_fail_open_on_garbage():
    store = MemStore()
    for body in ({}, {"messages": "nope"}, {"messages": [{"role": 7}]}, {"messages": [None]}):
        new_body, pending, stats = spine.transform(body, store)
        assert new_body is body
        assert not stats["applied"]


def test_oversized_capsule_is_not_substituted():
    store = MemStore()
    msgs = convo(4)
    completed, _ = spine.split_exchanges(msgs)
    for ex in completed[:-1]:
        h = spine.exchange_hash(ex)
        store.put_capsule(h, "waffle " * 500 + f"[ref:{h}]")
    _, _, stats = spine.transform({"model": "m", "messages": msgs}, store)
    assert not stats["applied"] and stats["reason"] == "not_smaller"


def test_exchange_hash_is_deterministic_and_content_addressed():
    ex = [user("q"), assistant("a")]
    assert spine.exchange_hash(ex) == spine.exchange_hash(json.loads(json.dumps(ex)))
    assert spine.exchange_hash(ex) != spine.exchange_hash([user("q"), assistant("b")])


# ---------------------------------------------------------------- store


def test_store_roundtrip_and_frozen_capsules(tmp_path):
    store = CapsuleStore(tmp_path / "proxy")
    ex = [user("q " * 50), assistant("a " * 50)]
    h = spine.exchange_hash(ex)
    store.put_original(h, ex)
    assert store.get_original(h) == ex
    assert store.pending() == [h]
    store.put_capsule(h, "first capsule")
    store.put_capsule(h, "second write must lose")
    assert store.get_capsule(h) == "first capsule"
    assert store.pending() == []


def test_store_rejects_unsafe_hashes(tmp_path):
    store = CapsuleStore(tmp_path / "proxy")
    store.put_capsule("../../evil", "x")
    store.put_original("ZZ", [])
    assert store.get_capsule("../../evil") is None
    assert list((tmp_path / "proxy" / "capsules").iterdir()) == []


# ---------------------------------------------------------------- compressor


def test_compress_exchange_shrinks_and_carries_ref():
    ex = [
        user("Please fix the bug in src/burnless/routing.py — scoring is wrong.\n" + "filler words " * 200),
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Fixed: the score in routing.py used max instead of sum. " + "detail " * 200},
                {"type": "tool_use", "id": "t", "name": "Edit", "input": {"x": 1}},
            ],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "huge output " * 300}]},
        assistant("Done."),
    ]
    h = spine.exchange_hash(ex)
    cap = compress_exchange(ex, h)
    assert cap is not None
    assert cap.endswith(f"[ref:{h}]")
    assert len(cap) < len(json.dumps(ex)) * 0.2
    assert "routing.py" in cap  # salient identifiers survive extraction
    assert "T: Edit" in cap


def test_compress_exchange_never_raises_on_junk():
    result = compress_exchange([{"role": "user"}, 42, None], "abcdefabcdef")
    assert result is None or isinstance(result, str)
    cap = compress_exchange([user(""), assistant("")], "abcdefabcdef")
    assert cap is None  # nothing to say → no capsule, exchange stays verbatim


def test_compressor_thread_end_to_end(tmp_path):
    store = CapsuleStore(tmp_path / "proxy")
    ex = [user("q " * 100), assistant("a " * 100)]
    h = spine.exchange_hash(ex)
    store.put_original(h, ex)
    worker = CompressorThread(store)
    worker.start()
    worker.enqueue([h, h])  # dedupe path
    for _ in range(200):
        if store.get_capsule(h):
            break
        import time

        time.sleep(0.01)
    cap = store.get_capsule(h)
    assert cap and cap.endswith(f"[ref:{h}]")


# ------------------------------------------------- review regressions (2026-07-31)


def test_split_user_message_mixing_tool_result_and_text_stays_in_flight():
    # Claude Code packs tool_result + a text block (system reminders) in one
    # user message; splitting there orphans the tool_use pair → API 400.
    msgs = [
        user("do the thing"),
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
                {"type": "text", "text": "<system-reminder>todo state</system-reminder>"},
            ],
        },
        assistant("done"),
        user("next"),
    ]
    completed, tail = spine.split_exchanges(msgs)
    assert len(completed) == 1 and len(completed[0]) == 4
    assert tail == [msgs[-1]]


def test_exchange_hash_ignores_client_cache_control():
    # Clients roll breakpoints forward every turn; the hash must not follow.
    ex_plain = [
        {"role": "user", "content": [{"type": "text", "text": "q"}]},
        assistant("a"),
    ]
    ex_marked = [
        {"role": "user", "content": [{"type": "text", "text": "q", "cache_control": {"type": "ephemeral"}}]},
        assistant("a"),
    ]
    assert spine.exchange_hash(ex_plain) == spine.exchange_hash(ex_marked)


def test_non_ascii_shrink_gate_is_honest():
    # Escaped-JSON chars vs raw chars once inflated the "before" side 5x for
    # CJK; both sides now use the same approx-token units, so a capsule that
    # would grow the request is refused.
    store = MemStore()
    msgs = []
    for i in range(4):
        msgs.append(user(f"質問{i}:" + "短い日本語の文章です。" * 3))
        msgs.append(assistant(f"回答{i}:" + "簡潔な返答。" * 3))
    msgs.append(user("次の質問"))
    completed, _ = spine.split_exchanges(msgs)
    for ex in completed[:-1]:
        h = spine.exchange_hash(ex)
        store.put_capsule(h, "U: 質問への長い長い要約 " * 12 + f"[ref:{h}]")
    _, _, stats = spine.transform({"model": "m", "messages": msgs}, store)
    assert not stats["applied"] and stats["reason"] == "not_smaller"


def test_non_text_exchange_gets_placeholder_capsule_and_never_stalls():
    ex = [
        {"role": "user", "content": [{"type": "image", "source": {"type": "base64", "data": "x" * 5000}}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "Look", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "seen"}]},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "private", "signature": "s"}]},
    ]
    h = spine.exchange_hash(ex)
    cap = compress_exchange(ex, h)
    assert cap is not None and cap.endswith(f"[ref:{h}]")
    assert "image" in cap and "non-text" in cap


def test_absorption_requires_recoverable_original():
    class AmnesiacStore(MemStore):
        def put_original(self, h, ex):  # disk full / read-only FS
            pass

    store = AmnesiacStore()
    msgs = convo(4)
    completed, _ = spine.split_exchanges(msgs)
    for ex in completed[:-1]:
        h = spine.exchange_hash(ex)
        store.put_capsule(h, compress_exchange(ex, h))
    _, _, stats = spine.transform({"model": "m", "messages": msgs}, store)
    # capsules exist but no verbatim can be persisted → nothing is absorbed
    assert not stats["applied"] and stats["reason"] == "no_capsules_yet"


def test_raw_retention_none_keeps_verbatim_off_disk(tmp_path):
    store = CapsuleStore(tmp_path / "proxy", raw_retention="none")
    ex = [user("secret " * 50), assistant("answer " * 50)]
    h = spine.exchange_hash(ex)
    store.put_original(h, ex)
    assert store.get_original(h) == ex  # in-memory, so the compressor still works
    assert list((tmp_path / "proxy" / "exchanges").iterdir()) == []
    # and the spine still absorbs: the operator chose capsule-only history
    msgs = convo(4)
    for e in spine.split_exchanges(msgs)[0][:-1]:
        hh = spine.exchange_hash(e)
        store.put_capsule(hh, compress_exchange(e, hh))
    _, _, stats = spine.transform({"model": "m", "messages": msgs}, store)
    assert stats["applied"]


def test_capsule_freeze_survives_concurrent_writers(tmp_path):
    store = CapsuleStore(tmp_path / "proxy")
    h = "ab" * 6
    results = []

    def write(text):
        store.put_capsule(h, text)
        results.append(store.get_capsule(h))

    threads = [__import__("threading").Thread(target=write, args=(f"capsule-{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    final = store.get_capsule(h)
    assert final is not None
    assert all(r == final for r in results[-3:])  # once frozen, every read agrees


# ---------------------------------------------------------------- server rewrite seam


def test_server_rewrite_only_touches_messages_path(tmp_path):
    pytest.importorskip("httpx")
    from burnless.proxy.server import SpineProxy

    proxy = SpineProxy(tmp_path / "proxy", upstream="http://127.0.0.1:1")
    raw = json.dumps({"model": "m", "messages": convo(1)}).encode()
    out, stats = proxy.rewrite("/v1/models", raw)
    assert out == raw and stats["reason"] == "not_messages"
    out, stats = proxy.rewrite("/v1/messages", b"not json {{{")
    assert out == b"not json {{{" and stats["reason"] == "unparseable"
    out, stats = proxy.rewrite("/v1/messages", raw)
    assert out == raw and not stats["applied"]  # turn 1 passthrough, byte-identical
    # SDK beta namespace posts /v1/messages?beta=true — still a messages call
    out, stats = proxy.rewrite("/v1/messages?beta=true", raw)
    assert stats["reason"] != "not_messages"
    assert "bytes_in" in stats  # every messages row carries the baseline
