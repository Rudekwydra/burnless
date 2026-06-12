"""Deterministic check primitives for ## Verify blocks.

Each subcommand exits 0 when the asserted condition HOLDS (the desired state),
1 when it does not, and 2 on a malformed invocation (missing file / bad args).
This kills hand-rolled grep/jq/python footguns where exit-1-on-good-state
produced false PART/ERR. Use these inside ## Verify instead of raw grep/jq.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _check_present(args) -> int:
    try:
        text = _read(args.file)
    except OSError as e:
        print(f"check present: cannot read {args.file}: {e}", file=sys.stderr)
        return 2
    if args.pattern in text:
        return 0
    print(f"check present: not found in {args.file}: {args.pattern!r}", file=sys.stderr)
    return 1


def _check_absent(args) -> int:
    try:
        text = _read(args.file)
    except OSError as e:
        print(f"check absent: cannot read {args.file}: {e}", file=sys.stderr)
        return 2
    if args.pattern not in text:
        return 0
    print(f"check absent: present in {args.file}: {args.pattern!r}", file=sys.stderr)
    return 1


def _check_file_exists(args) -> int:
    if Path(args.path).exists():
        return 0
    print(f"check file-exists: missing {args.path}", file=sys.stderr)
    return 1


def _dig(obj, path: str):
    cur = obj
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                raise KeyError(part)
        elif isinstance(cur, dict):
            if part not in cur:
                raise KeyError(part)
            cur = cur[part]
        else:
            raise KeyError(part)
    return cur


def _check_json_path(args) -> int:
    try:
        data = json.loads(_read(args.file))
    except OSError as e:
        print(f"check json-path: cannot read {args.file}: {e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"check json-path: invalid JSON in {args.file}: {e}", file=sys.stderr)
        return 2
    try:
        val = _dig(data, args.path)
    except KeyError as e:
        print(f"check json-path: path not found: {args.path} (missing {e})", file=sys.stderr)
        return 1
    if args.equals is not None and str(val) != args.equals:
        print(f"check json-path: {args.path} = {val!r}, expected {args.equals!r}", file=sys.stderr)
        return 1
    return 0


def _check_syntax(args) -> int:
    path = args.file
    suffix = Path(path).suffix.lower()
    try:
        text = _read(path)
    except OSError as e:
        print(f"check syntax: cannot read {path}: {e}", file=sys.stderr)
        return 2
    try:
        if suffix == ".py":
            compile(text, path, "exec")
        elif suffix == ".json":
            json.loads(text)
        elif suffix in (".yaml", ".yml"):
            import yaml
            yaml.safe_load(text)
        else:
            print(f"check syntax: unsupported extension {suffix!r} for {path}", file=sys.stderr)
            return 2
    except SyntaxError as e:
        print(f"check syntax: {path}: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"check syntax: {path}: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_check(args) -> int:
    sub = getattr(args, "check_cmd", None)
    fn = {
        "present": _check_present,
        "absent": _check_absent,
        "file-exists": _check_file_exists,
        "json-path": _check_json_path,
        "syntax": _check_syntax,
    }.get(sub)
    if fn is None:
        print("usage: burnless check {present|absent|file-exists|json-path|syntax} ...", file=sys.stderr)
        return 2
    return fn(args)


def register_check_parser(sub) -> None:
    cp = sub.add_parser("check", help="deterministic ## Verify primitives (exit 0 = desired state holds)")
    cp.set_defaults(func=lambda args, parser=cp: parser.print_help() or 0)
    csub = cp.add_subparsers(dest="check_cmd")

    p = csub.add_parser("present", help="assert PATTERN appears in FILE")
    p.add_argument("pattern")
    p.add_argument("file")
    p.set_defaults(func=cmd_check)

    p = csub.add_parser("absent", help="assert PATTERN does NOT appear in FILE")
    p.add_argument("pattern")
    p.add_argument("file")
    p.set_defaults(func=cmd_check)

    p = csub.add_parser("file-exists", help="assert PATH exists")
    p.add_argument("path")
    p.set_defaults(func=cmd_check)

    p = csub.add_parser("json-path", help="assert dotted PATH exists in JSON FILE (optionally --equals VALUE)")
    p.add_argument("file")
    p.add_argument("path")
    p.add_argument("--equals", default=None)
    p.set_defaults(func=cmd_check)

    p = csub.add_parser("syntax", help="assert FILE parses (.py/.json/.yaml)")
    p.add_argument("file")
    p.set_defaults(func=cmd_check)
