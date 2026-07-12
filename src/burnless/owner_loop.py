"""Owner-loop step 3d: refine_seed() — activate owner loop with injected rewriter."""

import json
from pathlib import Path

from .owner_cache import compute_base_fingerprint, write_refined_seed
from .owner_validate import validate_owner_output
from .epochs_v2 import living_rewrite_prompt_v3
from .markers import to_pt_markers


def log_owner_event(root, event: dict) -> None:
    """Append JSON-encoded event to .burnless/owner_loop.jsonl. Never raises."""
    try:
        root_path = Path(root) if isinstance(root, str) else root
        log_dir = root_path if root_path.name == ".burnless" else root_path / ".burnless"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "owner_loop.jsonl"
        with open(log_file, 'a', encoding='utf-8') as f:
            json.dump(event, f, ensure_ascii=False)
            f.write('\n')
    except Exception:
        pass


def refine_seed(
    cache_path: str,
    predecessors: list,
    floor_md: str,
    rewriter,
    owner_model: str,
    generated_at: str,
    exchange: str = "",
    prompt_version: str = "v3",
    root=None,
) -> bool:
    """
    Activate owner-loop: rewrite floor → validate → cache refined seed.

    Fail-closed: any error (rewriter exception, empty output, validation failure)
    returns False without writing cache.

    Args:
        cache_path: Path to write JSON cache.
        predecessors: List of (chat_id, living_doc_text) tuples, newest-first.
        floor_md: Floor markdown (previous validated state).
        rewriter: Callable that receives prompt str, returns markdown str or None.
        owner_model: Model identifier (stored in cache).
        generated_at: ISO timestamp (injected by caller, not generated here).
        exchange: Optional context exchange for rewrite prompt.
        prompt_version: Prompt version tag for fingerprint (default "v3").
        root: Project root for telemetry logging (optional; if None, logging is skipped).

    Returns:
        True if refined seed written to cache (safe != floor).
        False if: rewriter failed, returned empty/None, validation collapsed to floor,
                  or safe identical to floor (redundant cache).
    """

    try:
        # 1. Build rewrite prompt
        prompt = living_rewrite_prompt_v3(floor_md, exchange)

        # 2. Call rewriter; fail-closed if exception or empty
        candidate = rewriter(prompt)
        if not candidate:
            if root:
                log_owner_event(root, {"phase": "refine", "result": "empty"})
            return False

        candidate = to_pt_markers(candidate)

        # 3. Validate output; bars hallucination
        safe = validate_owner_output(floor_md, candidate)

        # 4. If safe == floor (validator rejected all or trivial refinement) → no cache
        if safe.strip() == floor_md.strip():
            if root:
                log_owner_event(root, {"phase": "refine", "result": "rejected_to_floor"})
            return False

        # 5. Write refined seed to cache
        fp = compute_base_fingerprint(predecessors, owner_model=owner_model, prompt_version=prompt_version)
        write_refined_seed(cache_path, safe, fp, owner_model, generated_at)

        if root:
            log_owner_event(root, {"phase": "refine", "result": "written", "fingerprint": fp})

        return True
    except Exception:
        # Fail-closed: any error (rewriter exception, IO, etc.) → return False
        return False
