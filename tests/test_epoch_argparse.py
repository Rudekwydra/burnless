from __future__ import annotations

import pytest

from burnless import cli


def test_flags_after_subcommand():
    """Test that --chat-id and --root work AFTER the subcommand (e.g., capture)."""
    parser = cli.build_parser()
    args = parser.parse_args(['epoch', 'capture', '--chat-id', 'abc', '--root', '/tmp/x'])
    assert args.chat_id == 'abc'
    assert args.root == '/tmp/x'
    assert args.epoch_cmd == 'capture'


def test_status_flags_after():
    """Test that --root works AFTER the status subcommand."""
    parser = cli.build_parser()
    args = parser.parse_args(['epoch', 'status', '--root', '/tmp/y'])
    assert args.root == '/tmp/y'
    assert args.epoch_cmd == 'status'
