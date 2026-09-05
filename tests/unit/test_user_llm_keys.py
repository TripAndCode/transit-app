import os

os.environ.setdefault("LLM_KEY_ENCRYPTION_KEY", "zJj1v3nq7v3rj0aWq2p8m9s4b6d5f7h9k1n3q5s7u9w=")

from pipeline.query.user_llm_keys import decrypt_key, encrypt_key, key_suffix


def test_encrypt_then_decrypt_roundtrips():
    raw = "gsk_test_abcdef1234567890"
    blob = encrypt_key(raw)
    assert blob != raw.encode()
    assert decrypt_key(blob) == raw


def test_key_suffix_is_last_four_chars():
    assert key_suffix("gsk_test_abcd1234") == "1234"


def test_encrypted_blob_never_contains_plaintext_key():
    raw = "gsk_super_secret_value"
    blob = encrypt_key(raw)
    assert raw.encode() not in blob
