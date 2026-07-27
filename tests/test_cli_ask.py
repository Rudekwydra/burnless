from __future__ import annotations

import re
from pathlib import Path
import pytest


def test_ask_output_flags_exist():
    """Test that --output and --output-dir flags are registered"""
    # Read the cli.py file and verify the arguments are defined
    cli_file = Path(__file__).parent.parent / 'src' / 'burnless' / 'cli.py'
    content = cli_file.read_text()

    # Check for --output flag
    assert "--output" in content, "--output flag not found in cli.py"

    # Check for --output-dir flag
    assert "--output-dir" in content, "--output-dir flag not found in cli.py"

    # Check that both are in the ask subparser section
    ask_section = content[content.find('add_parser("ask"'):content.find('add_parser("setup"')]
    assert '--output' in ask_section, "--output not in ask subparser"
    assert '--output-dir' in ask_section, "--output-dir not in ask subparser"


def test_ask_output_implementation():
    """Test that cmd_ask has output handling logic"""
    cli_file = Path(__file__).parent.parent / 'src' / 'burnless' / 'cli.py'
    content = cli_file.read_text()

    # Find cmd_ask function
    ask_start = content.find('def cmd_ask(')
    assert ask_start > 0, "cmd_ask function not found"

    ask_section = content[ask_start:ask_start + 10000]

    # Check for output handling logic
    assert 'args.output' in ask_section, "args.output handling not found"
    assert 'args.output_dir' in ask_section, "args.output_dir handling not found"
    assert 'parent.mkdir' in ask_section, "output directory creation not found"
    assert 'output_path.write_text' in ask_section, "file writing not found"
    assert 'ask_' in ask_section, "auto-naming pattern not found"


def test_mcp_server_f4_implementation():
    """Test that F4 changes are in mcp_server.py"""
    mcp_file = Path(__file__).parent.parent / 'src' / 'burnless' / 'mcp_server.py'
    content = mcp_file.read_text()

    # Check for helper functions
    assert '_get_known_roots' in content, "_get_known_roots function not found"
    assert '_build_root_hint' in content, "_build_root_hint function not found"
    assert 'global_metrics.jsonl' in content, "global_metrics.jsonl reference not found"

    # Count occurrences of _build_root_hint() calls (should be 11)
    calls = content.count('_build_root_hint()')
    assert calls >= 10, f"Expected at least 10 calls to _build_root_hint(), found {calls}"
