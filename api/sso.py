"""Which sign-in configuration this process has, and the key that signs its cookies.

Read from the environment on every call, so a test's monkeypatch and a
runtime config change both take effect without re-importing the app. All
four OAuth client variables set means Google/GitHub sign-in is on; none set
means it is off; a partial set is refused at startup by ``api.main.lifespan``.

``SESSION_SIGNING_KEY`` is not one of them: every sign-in method needs it,
the break-glass local admin included, and startup refuses a missing or dev
key whenever any sign-in method is on.
"""

import os

SIGNING_KEY_ENV = "SESSION_SIGNING_KEY"

# The public placeholder used when SESSION_SIGNING_KEY is unset. Anything it
# signs is forgeable, so it is acceptable only while no sign-in method is on;
# api.main's startup validation enforces that.
DEV_SIGNING_KEY = "dev-only-not-secret"

SSO_ENV = (
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
)


def sso_status() -> tuple[bool, list[str]]:
    """``(enabled, missing)``: enabled iff every OAuth client variable in ``SSO_ENV`` is set."""
    missing = [k for k in SSO_ENV if not os.environ.get(k)]
    return (not missing, missing)


def configured_signing_key() -> str | None:
    """The operator's ``SESSION_SIGNING_KEY``, or None when it is unset or blank.

    ``.env.example`` ships the variable empty, so a copied ``.env`` hands the
    process an empty string rather than leaving it unset."""
    key = os.environ.get(SIGNING_KEY_ENV, "")
    return key if key.strip() else None


def session_signing_key() -> str:
    """The key every cookie signer and keyed digest uses: the configured one, else the dev placeholder."""
    return configured_signing_key() or DEV_SIGNING_KEY
