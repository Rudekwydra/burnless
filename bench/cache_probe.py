#!/usr/bin/env python3
"""Phase-1 probe: why did solo-Opus never read cache in tier_economics?

Sonnet 5 read cache normally with identical code, and Opus 5 reads normally
in a plain two-turn conversation — so the failure lives in some interaction
between Opus 5 and the harness's message shape. Three arms isolate it, each
replaying the exact failing shape for 3 turns:

  X  as-failed      tool_use/tool_result blocks in history, NO tools declared
  Y  tools-declared same history, but the request declares the Read tool
  Z  text-only      same byte sizes, tool blocks replaced by plain text

Expected readings (pre-registered):
  X fails, Y reads  -> cause: tool blocks without tools declared; fix = declare
  X fails, Z reads  -> cause: tool blocks themselves; harness must declare tools
                       AND that is also the honest baseline (real clients do)
  all three read    -> anomaly was transient/infra; re-run tier bench as-is
  all three fail    -> STOP: undocumented Opus 5 behavior; no more Opus arms
                       until understood

Costs ~$0.25. Requires ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import os
import sys
import time

import httpx

API = "https://api.anthropic.com/v1/messages"
MODEL = "claude-opus-5"

TOOLS = [{
    "name": "Read",
    "description": "Read a file from the local filesystem and return its contents.",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "File path to read"}},
        "required": ["path"],
    },
}]


def payload(n: int = 70) -> str:
    return "\n".join(
        f"{i:>4}  def handler_{i}(payload, *, retries=3):\n"
        f"          if not payload: return None\n"
        f"          return dispatch(payload, retries=retries)"
        for i in range(n)
    )


def history(turn: int, tool_blocks: bool) -> list:
    """The tier-bench conversation shape after `turn` completed subtasks."""
    msgs: list = []
    for i in range(turn + 1):
        msgs.append({"role": "user", "content": [{"type": "text", "text": f"Step {i}: audit module {i}."}]})
        if i == turn:
            break
        if tool_blocks:
            msgs += [
                {"role": "assistant", "content": [
                    {"type": "text", "text": f"Looking at module {i}."},
                    {"type": "tool_use", "id": f"t{i}", "name": "Read", "input": {"path": f"src/mod_{i}.py"}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": f"t{i}", "content": payload()}]},
            ]
        else:
            msgs += [
                {"role": "assistant", "content": [
                    {"type": "text", "text": f"Looking at module {i}.\n[reading src/mod_{i}.py]"}]},
                {"role": "user", "content": [{"type": "text", "text": f"[file contents]\n{payload()}"}]},
            ]
        msgs.append({"role": "assistant", "content": [
            {"type": "text", "text": f"Done on module {i}: retry counter fixed, regression test added."}]})
    return msgs


def mark(messages: list, prev: int | None) -> list:
    out = [{**m, "content": [dict(b) for b in m["content"]]} for m in messages]
    out[-1]["content"][-1] = dict(out[-1]["content"][-1], cache_control={"type": "ephemeral"})
    if prev is not None and 0 <= prev < len(out) - 1:
        out[prev]["content"][-1] = dict(out[prev]["content"][-1], cache_control={"type": "ephemeral"})
    return out


def run_arm(client: httpx.Client, key: str, label: str, tool_blocks: bool, declare: bool, nonce: str) -> bool:
    system = [{"type": "text",
               "text": f"You are a senior engineer working through a task list.\nsession-nonce: {label}-{nonce}\n",
               "cache_control": {"type": "ephemeral"}}]
    print(f"\narm {label} (tool_blocks={tool_blocks}, tools_declared={declare})")
    tails, ok = [], True
    for turn in range(3):
        msgs = history(turn, tool_blocks)
        body = {"model": MODEL, "max_tokens": 8, "system": system,
                "messages": mark(msgs, tails[-1] if tails else None),
                "thinking": {"type": "disabled"}, "output_config": {"effort": "low"}}
        if declare:
            body["tools"] = TOOLS
        r = client.post(API, headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                      "content-type": "application/json"}, json=body, timeout=180)
        r.raise_for_status()
        u = r.json()["usage"]
        tails.append(len(msgs) - 1)
        read, write = u.get("cache_read_input_tokens", 0), u.get("cache_creation_input_tokens", 0)
        verdict = "frio (ok)" if turn == 0 else ("LEU cache" if read else "*** SEM READ ***")
        if turn > 0 and read == 0:
            ok = False
        print(f"  turn {turn}: read {read:>6,}  write {write:>6,}  fresh {u.get('input_tokens', 0):>4,}  {verdict}")
    return ok


def main() -> int:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 2
    nonce = str(int(time.time()))
    client = httpx.Client()
    results = {
        "X as-failed": run_arm(client, key, "X", tool_blocks=True, declare=False, nonce=nonce),
        "Y tools-declared": run_arm(client, key, "Y", tool_blocks=True, declare=True, nonce=nonce),
        "Z text-only": run_arm(client, key, "Z", tool_blocks=False, declare=False, nonce=nonce),
    }
    client.close()
    print("\n" + "=" * 50)
    for k, v in results.items():
        print(f"  {k:<18} {'cache OK' if v else 'cache FALHOU'}")
    x, y, z = results.values()
    if not x and y:
        print("\ncausa: blocos de tool sem `tools` declarado. Fix: declarar tools (fase 2 liberada).")
    elif not x and z:
        print("\ncausa: os próprios blocos de tool. Fix: declarar tools é obrigatório (fase 2 liberada).")
    elif x and y and z:
        print("\nanomalia não reproduziu — era transiente. Re-rodar tier bench como está.")
    else:
        print("\nNÃO EXPLICADO — parar braços Opus até entender (regra da fase 1).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
