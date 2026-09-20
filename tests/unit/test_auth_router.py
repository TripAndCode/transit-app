"""Unit tests for pure-Python helpers in api.routers.auth.

DB-backed login flows are covered by tests/api; these pin the two
import-time hygiene fixes that don't need a DB connection to verify:
the OAuth-transaction signer reading its key lazily, and the failed-
local-login audit fingerprint no longer retaining the raw username.
"""

from api.routers.auth import _get_signer, _username_fingerprint


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
