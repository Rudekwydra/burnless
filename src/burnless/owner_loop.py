"""Owner-loop step 3d: refine_seed() — activate owner loop with injected rewriter."""

from .owner_cache import compute_base_fingerprint, write_refined_seed
from .owner_validate import validate_owner_output
from .epochs_v2 import living_rewrite_prompt_v3


def refine_seed(
    cache_path: str,
    predecessors: list,
    floor_md: str,
    rewriter,
    owner_model: str,
    generated_at: str,
    exchange: str = "",
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
            return False

        # 3. Validate output; bars hallucination
        safe = validate_owner_output(floor_md, candidate)

        # 4. If safe == floor (validator rejected all or trivial refinement) → no cache
        if safe.strip() == floor_md.strip():
            return False

        # 5. Write refined seed to cache
        fp = compute_base_fingerprint(predecessors)
        write_refined_seed(cache_path, safe, fp, owner_model, generated_at)

        return True
    except Exception:
        # Fail-closed: any error (rewriter exception, IO, etc.) → return False
        return False
