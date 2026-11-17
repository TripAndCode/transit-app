"""Unit tests for pure-Python helpers in api.routers.auth.

DB-backed login flows are covered by tests/api; these pin the two
import-time hygiene properties that don't need a DB connection to verify:
the OAuth-transaction signer reads its key lazily, and the failed-
local-login audit fingerprint never retains the raw username.
"""

import pytest
from itsdangerous import BadSignature, URLSafeTimedSerializer

from api.routers.auth import _get_signer, _username_fingerprint
from api.sso import DEV_SIGNING_KEY


def test_get_signer_reads_session_signing_key_at_call_time(monkeypatch):
    monkeypatch.setenv("SESSION_SIGNING_KEY", "call-time-key-one")
    signer_a = _get_signer()
    assert signer_a.secret_keys == [b"call-time-key-one"]

    monkeypatch.setenv("SESSION_SIGNING_KEY", "call-time-key-two")
    signer_b = _get_signer()
    assert signer_b.secret_keys == [b"call-time-key-two"]


def test_get_signer_falls_back_to_dev_key_when_unset(monkeypatch):
    monkeypatch.delenv("SESSION_SIGNING_KEY", raising=False)
    signer = _get_signer()
    assert signer.secret_keys == [b"dev-only-not-secret"]


def test_get_signer_falls_back_to_dev_key_when_blank(monkeypatch):
    """`.env.example` ships the variable empty; an empty key would sign as
    forgeably as the dev key, so it is treated as unset."""
    monkeypatch.setenv("SESSION_SIGNING_KEY", "")
    assert _get_signer().secret_keys == [b"dev-only-not-secret"]


def test_get_signer_uses_the_key_without_its_surrounding_whitespace(monkeypatch):
    """The startup guard and every signer see one normalised value."""
    monkeypatch.setenv("SESSION_SIGNING_KEY", "  padded-key \r")
    assert _get_signer().secret_keys == [b"padded-key"]


def test_an_oauth_tx_cookie_forged_with_the_dev_key_is_rejected_under_a_real_key(monkeypatch):
    """The dev key is public, so anyone can sign with it; a deployment with a
    real key must not accept what it signed."""
    forged = URLSafeTimedSerializer(DEV_SIGNING_KEY, salt="oauth-tx").dumps({"state": "attacker"})
    monkeypatch.setenv("SESSION_SIGNING_KEY", "a-real-random-secret")
    with pytest.raises(BadSignature):
        _get_signer().loads(forged, max_age=60)


def test_get_signer_round_trips_payload(monkeypatch):
    monkeypatch.setenv("SESSION_SIGNING_KEY", "round-trip-key")
    token = _get_signer().dumps({"state": "s"})
    assert _get_signer().loads(token, max_age=60) == {"state": "s"}


def test_username_fingerprint_is_not_the_raw_username():
    """A password typed into the username field by mistake must not be
    retained verbatim in the audit trail."""
    fp = _username_fingerprint("Not-My-Password123!")
    assert fp != "Not-My-Password123!"
    assert "Not-My-Password123!" not in fp


def test_username_fingerprint_is_stable_and_case_insensitive():
    assert _username_fingerprint("Root@Local") == _username_fingerprint("root@local")


def test_username_fingerprint_is_16_hex_chars():
    fp = _username_fingerprint("someone@example.com")
    assert len(fp) == 16
    int(fp, 16)  # raises ValueError if not hex


def test_username_fingerprint_is_keyed_not_a_bare_digest(monkeypatch):
    """The fingerprint must not be recoverable by hashing a wordlist.

    This value is sometimes a password — the username field is where a
    mistyped one lands — so a bare digest would leave exactly that input
    open to an offline dictionary attack on the audit table, which is the
    thing the fingerprint exists to prevent.
    """
    import hashlib

    monkeypatch.setenv("SESSION_SIGNING_KEY", "key-one")
    fp = _username_fingerprint("Root@Local")
    assert fp != hashlib.sha256(b"root@local").hexdigest()[:16]

    # Same input under a different deployment key gives a different value.
    monkeypatch.setenv("SESSION_SIGNING_KEY", "key-two")
    assert _username_fingerprint("Root@Local") != fp


def test_username_fingerprint_is_case_insensitive_and_stable(monkeypatch):
    """Repeated attempts on one account still correlate in the audit trail."""
    monkeypatch.setenv("SESSION_SIGNING_KEY", "key-one")
    assert _username_fingerprint("Root@Local") == _username_fingerprint("root@local")
