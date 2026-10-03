"""Tests for the shared bearer-credential digest helper."""

import hashlib

from api.security import token_hash


def test_token_hash_is_sha256_hex():
    """The digest must be plain SHA-256 hex so SQL can reproduce it with
    ``encode(sha256(convert_to(<col>,'UTF8')),'hex')`` during backfill."""
    raw = "s6Bh-dR8Xk"
    assert token_hash(raw) == hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_token_hash_length_and_alphabet():
    digest = token_hash("anything")
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_token_hash_is_deterministic():
    assert token_hash("same") == token_hash("same")


def test_token_hash_distinguishes_inputs():
    assert token_hash("a") != token_hash("b")


def test_token_hash_handles_non_ascii():
    """UTF-8 is pinned explicitly so the Python and SQL digests agree
    regardless of the host's default encoding."""
    raw = "トークン"
    assert token_hash(raw) == hashlib.sha256(raw.encode("utf-8")).hexdigest()
