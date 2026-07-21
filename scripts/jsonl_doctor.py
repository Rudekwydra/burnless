"""
jsonl_doctor.py — rolling-memory transcript surgery for Claude Code JSONL files.

Preserves root + tail, compresses middle, maintains tool_use/tool_result pairing,
rewrites parentUuid chain, writes to a NEW file (never in-place).
"""

import argparse
import json
import os
import subprocess
import sys
import uuid

FLOOR_TOKENS = 39000
DEFAULT_N_TAIL = 8
DEFAULT_MIN_MIDDLE = 35000


def load_lines(path):
    """Read JSONL; skip empty lines and malformed JSON."""
    lines = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return lines


def current_context_tokens(lines):
    """Return token count from the last assistant entry that has message.usage."""
    for entry in reversed(lines):
        if entry.get("type") == "assistant":
            msg = entry.get("message", {})
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            if usage and isinstance(usage, dict):
                return (
                    usage.get("input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0)
                )
    return 0


def compressible_middle_tokens(lines):
    """Tokens above the irredutible floor — what can actually be compressed."""
    return max(0, current_context_tokens(lines) - FLOOR_TOKENS)


def should_rotate(lines, min_middle=DEFAULT_MIN_MIDDLE):
    """True when the compressible middle meets the rotation threshold."""
    return compressible_middle_tokens(lines) >= min_middle


def collect_tool_use_ids(entries):
    """Collect all tool_use block ids from a list of entries."""
    ids = set()
    for entry in entries:
        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                bid = block.get("id")
                if bid:
                    ids.add(bid)
    return ids


def collect_tool_result_refs(entries):
    """Collect all tool_use_id references from tool_result blocks."""
    refs = set()
    for entry in entries:
        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                ref = block.get("tool_use_id")
                if ref:
                    refs.add(ref)
    return refs


def _plain_text(entries):
    """Extract plain text from a list of entries for summarisation."""
    parts = []
    for entry in entries:
        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text") or (
                        block.get("content") if isinstance(block.get("content"), str) else ""
                    )
                    if text:
                        parts.append(str(text))
        elif isinstance(content, str) and content:
            parts.append(content)
    return " ".join(parts)


def compact_middle(middle_entries):
    """Summarise discarded middle entries. Tries ollama locally; falls back to extractive truncation."""
    model = os.environ.get("BURNLESS_BRONZE_OLLAMA_MODEL", "hf.co/unsloth/gemma-4-E4B-it-qat-GGUF:UD-Q4_K_XL")
    plain = _plain_text(middle_entries)
    prompt = f"Summarize this conversation context briefly (2-3 sentences):\n{plain[:3000]}"

    try:
        result = subprocess.run(
            ["ollama", "run", model, prompt],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    # Extractive fallback — never raises
    if not plain:
        return "(empty middle)"
    if len(plain) <= 1200:
        return plain
    return plain[:600] + " ... " + plain[-600:]


def _deep_copy_entry(entry):
    """JSON-round-trip copy — safe for well-formed JSONL entries."""
    return json.loads(json.dumps(entry))


def _entry_key(entry):
    """Dedup key: prefer message.id, fall back to entry uuid."""
    msg = entry.get("message")
    if isinstance(msg, dict):
        mid = msg.get("id")
        if mid:
            return mid
    return entry.get("uuid")


def _strip_orphan_results(entry, orphan_set):
    """Remove tool_result blocks whose tool_use_id is in orphan_set."""
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return entry
    content = msg.get("content")
    if not isinstance(content, list):
        return entry
    new_content = [
        b for b in content
        if not (
            isinstance(b, dict)
            and b.get("type") == "tool_result"
            and b.get("tool_use_id") in orphan_set
        )
    ]
    if len(new_content) == len(content):
        return entry
    entry = dict(entry)
    entry["message"] = {**msg, "content": new_content}
    return entry


def doctor(lines, n_tail=DEFAULT_N_TAIL):
    """
    Compress a transcript by discarding the middle, preserving root + tail.

    Returns (entries, new_sid).
    """
    if len(lines) < 2:
        # Nothing to compress; just rechain.
        entries = [_deep_copy_entry(e) for e in lines]
        new_sid = str(uuid.uuid4())
        prev = None
        for i, entry in enumerate(entries):
            new_u = str(uuid.uuid4())
            entry["parentUuid"] = entries[i - 1]["uuid"] if i > 0 else entry.get("parentUuid")
            entry["uuid"] = new_u
            entry["sessionId"] = new_sid
            prev = new_u
        return (entries, new_sid)

    actual_n_tail = min(n_tail, len(lines) - 1)
    root_orig = lines[0]
    root_parent_uuid = root_orig.get("parentUuid")

    if len(lines) <= actual_n_tail + 1:
        tail_entries = [_deep_copy_entry(e) for e in lines[1:]]
        middle_entries = []
    else:
        tail_entries = [_deep_copy_entry(e) for e in lines[-actual_n_tail:]]
        middle_entries = [_deep_copy_entry(e) for e in lines[1:-actual_n_tail]]

    root_entry = _deep_copy_entry(root_orig)

    # PAIR-SAFETY: find tool_result refs in preserved (root + tail) without a matching tool_use
    preserved_check = [root_entry] + tail_entries
    result_refs = collect_tool_result_refs(preserved_check)
    use_ids = collect_tool_use_ids(preserved_check)
    orphan_refs = result_refs - use_ids

    # Build lookup: tool_use_id → entry in middle
    middle_use_map = {}
    for entry in middle_entries:
        msg = entry.get("message", {})
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    bid = block.get("id")
                    if bid:
                        middle_use_map[bid] = entry

    # Drag back needed tool_use entries; collect truly unresolvable refs
    dragged_back = []
    still_orphan = set()
    seen_drag_keys = set()

    for ref in orphan_refs:
        if ref in middle_use_map:
            e = middle_use_map[ref]
            k = _entry_key(e)
            if k not in seen_drag_keys:
                dragged_back.append(e)
                if k:
                    seen_drag_keys.add(k)
        else:
            still_orphan.add(ref)

    # Strip truly unresolvable tool_results from root and tail
    if still_orphan:
        root_entry = _strip_orphan_results(root_entry, still_orphan)
        tail_entries = [_strip_orphan_results(e, still_orphan) for e in tail_entries]

    # Compact discarded middle
    summary_text = "[rolling-memory: miolo comprimido]\n" + compact_middle(middle_entries)

    # Assemble result: root → synthetic summary → dragged_back → tail
    result_entries = []
    seen_keys = set()

    def _add(e):
        k = _entry_key(e)
        if k and k in seen_keys:
            return
        if k:
            seen_keys.add(k)
        result_entries.append(e)

    _add(root_entry)

    synthetic = {
        "type": "user",
        "message": {
            "role": "user",
            "content": summary_text,
        },
    }
    result_entries.append(synthetic)

    for e in dragged_back:
        _add(e)

    for e in tail_entries:
        _add(e)

    # Rewrite sessionId / uuid / parentUuid for the entire chain
    new_sid = str(uuid.uuid4())
    prev_uuid = None

    for i, entry in enumerate(result_entries):
        new_u = str(uuid.uuid4())
        if i == 0:
            entry["parentUuid"] = root_parent_uuid
        else:
            entry["parentUuid"] = prev_uuid
        entry["uuid"] = new_u
        entry["sessionId"] = new_sid
        prev_uuid = new_u

    return (result_entries, new_sid)


def validate(entries):
    """
    Check structural integrity of the result.

    Returns (ok: bool, errors: list[str]).
    """
    errors = []

    # JSON serialisability
    for i, entry in enumerate(entries):
        try:
            json.dumps(entry)
        except (TypeError, ValueError) as exc:
            errors.append(f"Entry {i} not JSON-serializable: {exc}")

    # parentUuid chain
    for i in range(1, len(entries)):
        expected = entries[i - 1].get("uuid")
        actual = entries[i].get("parentUuid")
        if actual != expected:
            errors.append(
                f"Entry {i}: parentUuid={actual!r} != prev uuid={expected!r}"
            )

    # tool_result ordering: every ref must have a prior tool_use
    seen_use_ids = set()
    for i, entry in enumerate(entries):
        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                bid = block.get("id")
                if bid:
                    seen_use_ids.add(bid)
            elif btype == "tool_result":
                ref = block.get("tool_use_id")
                if ref and ref not in seen_use_ids:
                    errors.append(
                        f"Entry {i}: tool_result references {ref!r} with no prior tool_use"
                    )

    return (len(errors) == 0, errors)


def main():
    parser = argparse.ArgumentParser(
        description="Compress a Claude Code JSONL transcript for rolling memory."
    )
    parser.add_argument("input", help="Input JSONL file")
    parser.add_argument("--n-tail", type=int, default=DEFAULT_N_TAIL, dest="n_tail")
    parser.add_argument("--out", default=None)
    parser.add_argument("--min-middle", type=int, default=DEFAULT_MIN_MIDDLE, dest="min_middle")
    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    lines = load_lines(input_path)

    middle_tokens = compressible_middle_tokens(lines)
    rotate = should_rotate(lines, min_middle=args.min_middle)

    entries, new_sid = doctor(lines, n_tail=args.n_tail)

    if args.out is not None:
        out_path = os.path.abspath(args.out)
    else:
        out_dir = os.path.dirname(input_path)
        out_path = os.path.join(out_dir, f"{new_sid}.jsonl")

    if out_path == input_path:
        print("ERROR: --out must differ from input (never in-place)", file=sys.stderr)
        sys.exit(2)

    with open(out_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

    ok, errs = validate(entries)

    print(f"MIDDLE_TOKENS={middle_tokens}")
    print(f"SHOULD_ROTATE={rotate}")
    print(f"NEW_SID={new_sid}")
    print(f"OUT={out_path}")
    print(f"VALID={ok}")

    if not ok:
        for err in errs:
            print(f"  VALIDATION ERROR: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
