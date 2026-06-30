import os
from burnless.epochs import carry_forward_chain, epoch_dir
from burnless.epochs_v2 import living_path
from burnless.owner_cache import compute_base_fingerprint, write_refined_seed


def test_no_cache_falls_through_to_floor(tmp_path, monkeypatch):
    """Without refined_seed.json cache, carry_forward_chain returns deterministic consolidated living-doc."""
    monkeypatch.setenv("BURNLESS_EPOCH_V2", "1")

    # Create two predecessor chats with living.md documents
    living1_text = """## Foco atual
- task A
- task B

## Decisões
- chose impl X
"""
    living2_text = """## Foco atual
- task C

## Threads abertas
- waiting for PR
"""

    chat1_dir = epoch_dir(tmp_path, "chat1")
    chat1_dir.mkdir(parents=True, exist_ok=True)
    lp1 = living_path(tmp_path, "chat1")
    lp1.parent.mkdir(parents=True, exist_ok=True)
    lp1.write_text(living1_text, encoding='utf-8')

    chat2_dir = epoch_dir(tmp_path, "chat2")
    chat2_dir.mkdir(parents=True, exist_ok=True)
    lp2 = living_path(tmp_path, "chat2")
    lp2.parent.mkdir(parents=True, exist_ok=True)
    lp2.write_text(living2_text, encoding='utf-8')

    # Ensure NO cache exists
    cache_path = tmp_path / ".burnless" / "epochs" / "_rolling" / "refined_seed.json"
    assert not cache_path.exists()

    # Call carry_forward_chain
    result = carry_forward_chain(tmp_path, current_chat_id="chat_current")

    # Should return deterministic consolidated doc (contains consolidation header + sections)
    assert result  # Non-empty
    assert "## Foco atual" in result
    assert "ordem: documento vivo" in result or "consolidado" in result  # consolidation header
    # Should NOT contain sentinel (cache was not served)
    assert "REFINED_SENTINEL" not in result


def test_valid_cache_is_served(tmp_path, monkeypatch):
    """With valid refined_seed.json cache (matching fingerprint), carry_forward_chain returns it immediately."""
    monkeypatch.setenv("BURNLESS_EPOCH_V2", "1")

    living1_text = """## Foco atual
- task A
"""
    living2_text = """## Foco atual
- task B
"""

    chat1_dir = epoch_dir(tmp_path, "chat1")
    chat1_dir.mkdir(parents=True, exist_ok=True)
    lp1 = living_path(tmp_path, "chat1")
    lp1.parent.mkdir(parents=True, exist_ok=True)
    lp1.write_text(living1_text, encoding='utf-8')

    chat2_dir = epoch_dir(tmp_path, "chat2")
    chat2_dir.mkdir(parents=True, exist_ok=True)
    lp2 = living_path(tmp_path, "chat2")
    lp2.parent.mkdir(parents=True, exist_ok=True)
    lp2.write_text(living2_text, encoding='utf-8')

    # Compute exact fingerprint matching what carry_forward_chain will compute
    # (newest-first order, matching sort by mtime reversed)
    import time
    # Make chat2 newer than chat1
    lp1_mtime = time.time() - 100
    lp2_mtime = time.time()
    os.utime(str(lp1), (lp1_mtime, lp1_mtime))
    os.utime(str(lp2), (lp2_mtime, lp2_mtime))

    # Build predecessors in newest-first order (matching carry_forward_chain logic)
    predecessors = [
        ("chat2", living2_text),
        ("chat1", living1_text),
    ]
    fp = compute_base_fingerprint(predecessors)

    # Write refined seed cache with matching fingerprint and sentinel
    sentinel_seed = "## Foco atual\n- REFINED_SENTINEL\n"
    cache_path = tmp_path / ".burnless" / "epochs" / "_rolling" / "refined_seed.json"
    write_refined_seed(
        str(cache_path),
        sentinel_seed,
        fp,
        owner_model="test-model",
        generated_at="2026-06-30T00:00:00Z"
    )

    # Call carry_forward_chain
    result = carry_forward_chain(tmp_path, current_chat_id="chat_current")

    # Should return the cached refined seed immediately
    assert result == sentinel_seed
    assert "REFINED_SENTINEL" in result


def test_stale_cache_ignored(tmp_path, monkeypatch):
    """With stale refined_seed.json cache (fingerprint mismatch), carry_forward_chain falls back to floor."""
    monkeypatch.setenv("BURNLESS_EPOCH_V2", "1")

    living1_text = """## Foco atual
- task A
"""

    chat1_dir = epoch_dir(tmp_path, "chat1")
    chat1_dir.mkdir(parents=True, exist_ok=True)
    lp1 = living_path(tmp_path, "chat1")
    lp1.parent.mkdir(parents=True, exist_ok=True)
    lp1.write_text(living1_text, encoding='utf-8')

    # Write refined seed cache with WRONG fingerprint
    wrong_fp = "0000000000000000000000000000000000000000000000000000000000000000"
    sentinel_seed = "## Foco atual\n- REFINED_SENTINEL\n"
    cache_path = tmp_path / ".burnless" / "epochs" / "_rolling" / "refined_seed.json"
    write_refined_seed(
        str(cache_path),
        sentinel_seed,
        wrong_fp,
        owner_model="test-model",
        generated_at="2026-06-30T00:00:00Z"
    )

    # Call carry_forward_chain
    result = carry_forward_chain(tmp_path, current_chat_id="chat_current")

    # Should return deterministic floor (not the stale cache)
    assert result  # Non-empty
    assert "REFINED_SENTINEL" not in result  # Should NOT contain sentinel
    assert "## Foco atual" in result  # Should have deterministic structure
