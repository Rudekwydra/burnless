import pytest

from burnless.codec import cipher


def test_v2_capsule_does_not_embed_key():
    key = cipher.generate_key()
    ciphertext = cipher.encode("secret text", key)
    capsule = cipher.pack("abc123", key, ciphertext)

    assert capsule.startswith("burnless:v2:abc123:")
    assert key not in capsule

    session_id, decoded_key, decoded_ciphertext = cipher.unpack(capsule)
    assert session_id == "abc123"
    assert decoded_key == key
    assert cipher.decode(decoded_ciphertext, decoded_key) == "secret text"


def test_v2_capsule_requires_local_keyring():
    key = cipher.generate_key()
    ciphertext = cipher.encode("secret text", key)
    capsule = cipher.pack("abc123", key, ciphertext)
    _version, _session_id, kid = cipher.unpack_metadata(capsule)

    cipher.forget_key(kid)

    with pytest.raises(ValueError, match="missing local key"):
        cipher.unpack(capsule)


def test_v1_capsule_still_decodes_for_compatibility():
    key = cipher.generate_key()
    ciphertext = cipher.encode("legacy", key)
    capsule = cipher.pack("legacy-session", key, ciphertext, include_key=True)

    session_id, decoded_key, decoded_ciphertext = cipher.unpack(capsule)

    assert session_id == "legacy-session"
    assert decoded_key == key
    assert cipher.decode(decoded_ciphertext, decoded_key) == "legacy"
