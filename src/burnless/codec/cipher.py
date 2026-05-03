import base64
import secrets


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


def pack(session_id: str, key: str, ciphertext: str) -> str:
    return f"burnless:{session_id}:{key}:{ciphertext}"


def unpack(capsule: str) -> tuple[str, str, str]:
    parts = capsule.split(":", 3)
    if len(parts) != 4 or parts[0] != "burnless":
        raise ValueError(f"not a burnless capsule: {capsule[:40]}")
    return parts[1], parts[2], parts[3]
