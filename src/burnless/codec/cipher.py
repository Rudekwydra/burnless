"""RESERVED / DEPRECATED in burnless free.

The encrypted-capsule cipher (XOR + key custody) is NOT used by the live chat pipeline
(which decodes semantically via the Maestro decoder_hint). The standalone
`burnless compress`/`decode` CLI that used this is deprecated: key_store was memory-only
(_MEMORY_KEYS), so v2 capsules are not decodable across processes. The concept (encrypted
capsules + persistent key custody, the planned key_store=local) is migrated to the burnless
Pro / Synapsis roadmap. Code kept intact for that reuse. See capsule
burnless-cipher-decoder-deprecated-2026-06-10.
"""
import base64
import hashlib
import secrets


_MEMORY_KEYS: dict[str, str] = {}


def generate_key() -> str:
    return secrets.token_hex(16)


def encode(text: str, key: str) -> str:
    key_bytes = bytes.fromhex(key)
    text_bytes = text.encode("utf-8")
    xored = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(text_bytes))
    return base64.b64encode(xored).decode("ascii")


def decode(ciphertext: str, key: str) -> str:
    key_bytes = bytes.fromhex(key)
    xored = base64.b64decode(ciphertext)
    text_bytes = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(xored))
    return text_bytes.decode("utf-8")


def key_id(key: str) -> str:
    return hashlib.sha256(bytes.fromhex(key)).hexdigest()[:16]


def remember_key(key: str, *, kid: str | None = None) -> str:
    kid = kid or key_id(key)
    _MEMORY_KEYS[kid] = key
    return kid


def forget_key(kid: str) -> None:
    _MEMORY_KEYS.pop(kid, None)


def get_key(kid: str) -> str | None:
    return _MEMORY_KEYS.get(kid)


def pack_v1(session_id: str, key: str, ciphertext: str) -> str:
    return f"burnless:{session_id}:{key}:{ciphertext}"


def pack(session_id: str, key: str, ciphertext: str, *, include_key: bool = False) -> str:
    if include_key:
        return pack_v1(session_id, key, ciphertext)
    kid = remember_key(key)
    return f"burnless:v2:{session_id}:{kid}:{ciphertext}"


def unpack(capsule: str) -> tuple[str, str, str]:
    """Return (session_id, key, ciphertext).

    v1 capsules embed the key and remain decodable.
    v2 capsules carry only a key id; the key must be present in the in-memory
    keyring or supplied through another local key-custody path.
    """
    parts = capsule.split(":", 3)
    if len(parts) == 4 and parts[0] == "burnless" and parts[1] != "v2":
        return parts[1], parts[2], parts[3]

    parts_v2 = capsule.split(":", 4)
    if len(parts_v2) != 5 or parts_v2[0] != "burnless" or parts_v2[1] != "v2":
        raise ValueError(f"not a burnless capsule: {capsule[:40]}")
    _prefix, _version, session_id, kid, ciphertext = parts_v2
    key = get_key(kid)
    if not key:
        raise ValueError(f"missing local key for capsule key_id={kid}")
    return session_id, key, ciphertext


def unpack_metadata(capsule: str) -> tuple[str, str, str]:
    """Return (version, session_id, key_or_key_id) without revealing secret values."""
    parts_v2 = capsule.split(":", 4)
    if len(parts_v2) == 5 and parts_v2[0] == "burnless" and parts_v2[1] == "v2":
        return "v2", parts_v2[2], parts_v2[3]
    parts = capsule.split(":", 3)
    if len(parts) == 4 and parts[0] == "burnless":
        return "v1", parts[1], "embedded"
    raise ValueError(f"not a burnless capsule: {capsule[:40]}")
