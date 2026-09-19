import os

import pytest

os.environ.setdefault("LLM_KEY_ENCRYPTION_KEY", "zJj1v3nq7v3rj0aWq2p8m9s4b6d5f7h9k1n3q5s7u9w=")

from pipeline.query.user_llm_keys import _fernet, decrypt_key, encrypt_key, key_suffix, save_user_llm_key


def test_missing_encryption_key_names_the_variable(monkeypatch):
    """The failure has to say which variable is missing.

    This key is only reached on the BYOK screen, long after startup, so a
    misconfigured deploy learns about it from this message and nothing else --
    a bare KeyError from inside Fernet() names neither the variable nor how to
    generate a value.
    """
    _fernet.cache_clear()
    monkeypatch.delenv("LLM_KEY_ENCRYPTION_KEY", raising=False)
    try:
        with pytest.raises(RuntimeError, match="LLM_KEY_ENCRYPTION_KEY"):
            encrypt_key("gsk_test_abc")
    finally:
        _fernet.cache_clear()


def test_blank_encryption_key_is_treated_as_missing(monkeypatch):
    # An empty value in a .env file is the likeliest way to get here, and
    # Fernet("") would otherwise fail on key length instead.
    _fernet.cache_clear()
    monkeypatch.setenv("LLM_KEY_ENCRYPTION_KEY", "")
    try:
        with pytest.raises(RuntimeError, match="LLM_KEY_ENCRYPTION_KEY"):
            encrypt_key("gsk_test_abc")
    finally:
        _fernet.cache_clear()


def test_encrypt_then_decrypt_roundtrips():
    raw = "gsk_test_abcdef1234567890"
    blob = encrypt_key(raw)
    assert blob != raw.encode()
    assert decrypt_key(blob) == raw


def test_key_suffix_is_last_four_chars():
    assert key_suffix("gsk_test_abcd1234") == "1234"


def test_key_suffix_masks_inputs_at_or_under_four_chars():
    assert key_suffix("abcd") == "****"
    assert key_suffix("ab") == "**"


def test_encrypted_blob_never_contains_plaintext_key():
    raw = "gsk_super_secret_value"
    blob = encrypt_key(raw)
    assert raw.encode() not in blob


async def test_save_rejects_unsupported_provider():
    with pytest.raises(ValueError):
        await save_user_llm_key(None, 1, "unsupported-provider", "some_key")
