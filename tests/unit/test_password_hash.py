"""Tests for the scrypt-based password hash/verify helpers backing the local
break-glass admin login (api.security.hash_password / verify_password)."""

from api import security
from api.security import hash_password, verify_local_login_password, verify_password


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


def test_verify_local_login_password_known_user_calls_verify_once(monkeypatch):
    """A real stored hash is passed straight through to verify_password."""
    calls = []
    monkeypatch.setattr(security, "verify_password", lambda pw, stored: calls.append((pw, stored)) or True)
    stored = "scrypt$" + "aa" * 16 + "$" + "bb" * 32
    assert verify_local_login_password("pw", stored) is True
    assert calls == [("pw", stored)]


def test_verify_local_login_password_unknown_user_still_calls_verify_once(monkeypatch):
    """No stored hash (unknown username) must still perform exactly one scrypt
    verification, against the module-level dummy hash, so an unknown username
    takes the same time as a wrong password for a real one."""
    calls = []
    monkeypatch.setattr(security, "verify_password", lambda pw, stored: calls.append((pw, stored)) or False)
    assert verify_local_login_password("pw", None) is False
    assert len(calls) == 1
    assert calls[0] == ("pw", security._DUMMY_PASSWORD_HASH)


def test_dummy_password_hash_is_a_well_formed_scrypt_hash():
    """The dummy hash must be real output of hash_password, not a placeholder
    string, so the timing genuinely matches a real verification."""
    algo, salt_hex, hash_hex = security._DUMMY_PASSWORD_HASH.split("$")
    assert algo == "scrypt"
    bytes.fromhex(salt_hex)
    bytes.fromhex(hash_hex)
