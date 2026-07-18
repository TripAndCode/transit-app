"""Tests for the scrypt-based password hash/verify helpers backing the local
break-glass admin login (api.security.hash_password / verify_password)."""

from api.security import hash_password, verify_password


def test_verify_accepts_the_correct_password():
    stored = hash_password("correct-horse-battery-staple")
    assert verify_password("correct-horse-battery-staple", stored) is True


def test_verify_rejects_a_wrong_password():
    stored = hash_password("correct-horse-battery-staple")
    assert verify_password("wrong-password", stored) is False


def test_two_hashes_of_the_same_password_differ_by_salt():
    a = hash_password("same-password")
    b = hash_password("same-password")
    assert a != b
    assert verify_password("same-password", a) is True
    assert verify_password("same-password", b) is True


def test_verify_returns_false_not_raises_for_none():
    assert verify_password("anything", None) is False


def test_verify_returns_false_not_raises_for_malformed_hash():
    assert verify_password("anything", "not-a-valid-hash") is False
    assert verify_password("anything", "scrypt$onlyonefield") is False
    assert verify_password("anything", "wrongalgo$" + "aa" * 16 + "$" + "bb" * 32) is False
