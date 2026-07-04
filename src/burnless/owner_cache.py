import json
import hashlib
from pathlib import Path
from tempfile import NamedTemporaryFile


def compute_base_fingerprint(
    predecessors: list[tuple[str, str]], schema_version: str = "v3", owner_model: str = "", prompt_version: str = ""
) -> str:
    """
    Compute stable SHA256 fingerprint of predecessors list.

    Args:
        predecessors: List of (chat_id, living_doc_text) tuples, newest-first.
        schema_version: Version tag for schema compatibility (default "v3").
        owner_model: Model identifier used to generate seed (default "").
        prompt_version: Prompt version tag for schema compatibility (default "").

    Returns:
        Hex digest SHA256 of concatenated: schema_version + owner_model + prompt_version + each (chat_id + sha256(living_doc_text)).
        Order-sensitive; same input order -> same digest byte-for-byte.
        Changing owner_model or prompt_version changes digest (cache invalidation on model/prompt change).
    """
    h = hashlib.sha256()
    h.update(schema_version.encode())
    h.update(owner_model.encode())
    h.update(prompt_version.encode())
    for chat_id, living_doc_text in predecessors:
        h.update(chat_id.encode())
        h.update(hashlib.sha256(living_doc_text.encode()).digest())
    return h.hexdigest()


def write_refined_seed(
    cache_path: str, seed_md: str, fingerprint: str, owner_model: str, generated_at: str
) -> None:
    """
    Write refined seed to cache with atomic rename.
    
    Args:
        cache_path: Path to write JSON cache file.
        seed_md: Refined seed markdown content.
        fingerprint: Content fingerprint (from compute_base_fingerprint).
        owner_model: Model identifier used to generate seed.
        generated_at: ISO timestamp (injected by caller).
    
    Creates parent directories if missing. Writes to cache_path + ".tmp" then renames atomically.
    """
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    
    payload = {
        "seed_md": seed_md,
        "fingerprint": fingerprint,
        "owner_model": owner_model,
        "generated_at": generated_at,
        "schema_version": "v3",
    }
    
    tmp_path = cache_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    Path(tmp_path).rename(cache_path)


def read_valid_refined_seed(cache_path: str, current_fingerprint: str) -> str | None:
    """
    Read refined seed from cache, validating fingerprint.
    
    Args:
        cache_path: Path to JSON cache file.
        current_fingerprint: Expected fingerprint (from compute_base_fingerprint).
    
    Returns:
        seed_md if file exists, is valid JSON, and fingerprint matches current_fingerprint.
        None if file missing, JSON invalid, or fingerprint divergent (stale).
        Never raises exception to caller (fail-closed).
    """
    try:
        with open(cache_path, encoding="utf-8") as f:
            payload = json.load(f)
        
        if payload.get("fingerprint") == current_fingerprint:
            return payload.get("seed_md")
        return None
    except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError):
        return None