"""Layer 0 — characterize the provider cache primitive, per model.

Nothing above this layer can be trusted until this passes. The earlier benches
built on an unmeasured assumption ("caching just works") and the Opus 5 arm
silently never read cache. This tool answers, per model, the three questions
every bench above depends on — and resolves the Opus mystery by experiment
rather than by the hand-waved "probably the minimum" the plan doc had to
retract.

The refined hypothesis this tests: caching keys on the SEGMENT between
breakpoints meeting the model's minimum, and where the big content sits
relative to the breakpoint decides whether it caches.

  E2  big content INSIDE the marked message   (worked for Opus in a probe)
  E3  big content in an UNMARKED message, mark on a tiny message
                                              (the tier-bench shape — failed)
  E4  same as E3 but the breakpoint sits ON the big message
                                              (the candidate fix)

Reading:
  E2 read, E3 no-read, E4 read  -> primitive understood: the breakpoint must
     close a segment that itself exceeds the minimum. FIX: place breakpoints on
     (or after) the big blocks, never on tiny messages with big content beyond.
  E3 read too                   -> the tier-bench failure was something else;
     keep digging before building the combined bench.

Costs ~$0.15 for one model, ~$0.10 more per extra. --dry-run makes no calls.
"""
from __future__ import annotations

import argparse

import httpx

from harness import CacheGate, call, place_breakpoints, require_key, run_nonce


def big(n: int = 90) -> str:
    return "\n".join(
        f"{i:>4}  def handler_{i}(payload, *, retries=3):\n"
        f"          if not payload: return None\n"
        f"          return dispatch(payload, retries=retries)"
        for i in range(n)
    )


def system_block(label: str, nonce: str) -> list:
    # deliberately large enough to clear every model's minimum, so the system
    # entry is never the confound — we are testing MESSAGE-history caching
    text = ("You are a senior engineer working a task list. "
            "Keep the ledger honest and fail open on any error. " * 60) + f"\nnonce: {label}-{nonce}\n"
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def e2_history(turn: int) -> tuple[list, list]:
    """Big content INSIDE the marked user message. Mark rolls on those big
    messages. Returns (messages, breakpoint_indices)."""
    msgs, marks = [], []
    for i in range(turn + 1):
        msgs.append({"role": "user", "content": [{"type": "text", "text": f"Analyze this file:\n{big()}"}]})
        marks.append(len(msgs) - 1)
        if i == turn:
            break
        msgs.append({"role": "assistant", "content": [{"type": "text", "text": f"Analyzed file {i}."}]})
    return msgs, marks[-2:]  # prev + tail big messages


def e3_history(turn: int) -> tuple[list, list]:
    """Tier-bench shape: tiny marked user messages, big content in UNMARKED
    tool_result blocks between them."""
    msgs, marks = [], []
    for i in range(turn + 1):
        msgs.append({"role": "user", "content": [{"type": "text", "text": f"Step {i}: audit module {i}."}]})
        marks.append(len(msgs) - 1)
        if i == turn:
            break
        msgs += [
            {"role": "assistant", "content": [{"type": "text", "text": f"Reading module {i}."}]},
            {"role": "user", "content": [{"type": "text", "text": f"[contents of module {i}]\n{big()}"}]},
            {"role": "assistant", "content": [{"type": "text", "text": f"Done on module {i}."}]},
        ]
    return msgs, marks[-2:]  # prev + tail tiny messages


def e4_history(turn: int) -> tuple[list, list]:
    """Same content as E3 but the breakpoint sits ON the big unmarked message
    (the candidate fix): mark the big [contents] message, not the tiny Step."""
    msgs, big_marks = [], []
    for i in range(turn + 1):
        msgs.append({"role": "user", "content": [{"type": "text", "text": f"Step {i}: audit module {i}."}]})
        if i == turn:
            break
        msgs.append({"role": "assistant", "content": [{"type": "text", "text": f"Reading module {i}."}]})
        msgs.append({"role": "user", "content": [{"type": "text", "text": f"[contents of module {i}]\n{big()}"}]})
        big_marks.append(len(msgs) - 1)
        msgs.append({"role": "assistant", "content": [{"type": "text", "text": f"Done on module {i}."}]})
    marks = big_marks[-1:] + [len(msgs) - 1]  # last big message + tail
    return msgs, marks


EXPERIMENTS = {"E2 big-in-marked": e2_history, "E3 tier-shape": e3_history, "E4 mark-on-big": e4_history}


def run_experiment(client, key, model, name, build, nonce, turns=3):
    gate = CacheGate(name, model)
    for t in range(turns):
        msgs, marks = build(t)
        marked = place_breakpoints(msgs, marks)
        u = call(client, key, model, system_block(name, nonce), marked)
        gate.record(t, u, expect="cold" if t == 0 else "read")
    return gate


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--turns", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        print(f"would run E2/E3/E4 x {args.turns} turns on {args.model} "
              f"(~{3 * args.turns} calls, small max_tokens; est. $0.10-0.20)")
        return 0

    key = require_key()
    nonce = run_nonce()
    client = httpx.Client()
    print(f"layer 0 — cache primitive on {args.model}\n")
    gates = {}
    for name, build in EXPERIMENTS.items():
        print(f"{name}:")
        gates[name] = run_experiment(client, key, args.model, name, build, nonce, args.turns)
        print()
    client.close()

    print("=" * 56)
    verdicts = {k: g.passed() for k, g in gates.items()}
    for k, ok in verdicts.items():
        print(f"  {k:<20} {'message cache OK' if ok else 'NO message read'}")
    e2, e3, e4 = verdicts["E2 big-in-marked"], verdicts["E3 tier-shape"], verdicts["E4 mark-on-big"]
    print()
    if e2 and not e3 and e4:
        print("PRIMITIVE UNDERSTOOD: a breakpoint caches its history only when the")
        print("segment it closes exceeds the model minimum. Rule for every bench:")
        print("place breakpoints ON/after the big blocks, never on tiny messages")
        print("that have big content beyond them. The tier-bench USD for Opus was")
        print("invalid because its marks sat on tiny 'Step N' messages.")
    elif e2 and e3 and e4:
        print("All shapes cache on this model — the earlier Opus failure was")
        print("something else (or transient). Do NOT build on this until re-checked.")
    else:
        print(f"Unexpected pattern E2={e2} E3={e3} E4={e4} — characterize further")
        print("before trusting any paid bench on this model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
