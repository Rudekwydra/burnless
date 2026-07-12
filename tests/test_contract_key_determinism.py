import subprocess
import sys
import os
import pytest
from pathlib import Path


def test_contract_key_respects_text_order():
    """Test that contract_key returns the entity appearing first in the text."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from burnless.epochs_v2 import contract_key

    # Path appears first, then ID
    line1 = "Store config at /tmp/settings and use d123 for tracking"
    key1 = contract_key(line1)
    assert key1 == "/tmp/settings", f"Expected /tmp/settings but got {key1}"

    # ID appears first, then path
    line2 = "Ticket d567 should update /etc/config/main.yaml"
    key2 = contract_key(line2)
    assert key2 == "d567", f"Expected d567 but got {key2}"

    # Multiple entities: commit hash, path, ID (in that order)
    line3 = "Reference abc1234def at /Users/test/burnless/src and d999"
    key3 = contract_key(line3)
    assert key3 == "abc1234def", f"Expected abc1234def but got {key3}"


def test_contract_key_determinism_across_processes():
    """Test that contract_key is deterministic across Python processes with different PYTHONHASHSEED."""
    src_path = Path(__file__).parent.parent / "src"

    # Test line with 3 entities: path appears 1st, commit hash 2nd, ID 3rd
    test_line = "- Process /tmp/data with hash abc1234567 and ticket d555"

    # Run with PYTHONHASHSEED=1
    code1 = f"""
import sys
sys.path.insert(0, "{src_path}")
from burnless.epochs_v2 import contract_key
result = contract_key("{test_line}")
print(result)
"""
    result1 = subprocess.run(
        [sys.executable, "-c", code1],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONHASHSEED": "1"},
    )

    # Run with PYTHONHASHSEED=2
    code2 = f"""
import sys
sys.path.insert(0, "{src_path}")
from burnless.epochs_v2 import contract_key
result = contract_key("{test_line}")
print(result)
"""
    result2 = subprocess.run(
        [sys.executable, "-c", code2],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONHASHSEED": "2"},
    )

    out1 = result1.stdout.strip()
    out2 = result2.stdout.strip()

    assert result1.returncode == 0, f"Process 1 failed: {result1.stderr}"
    assert result2.returncode == 0, f"Process 2 failed: {result2.stderr}"
    assert out1 == out2, f"Determinism check failed: {out1} != {out2} (seeds 1 vs 2)"
    assert out1 == "/tmp/data", f"Expected /tmp/data but got {out1}"
