"""Which sign-in methods this process has, and the key that signs its cookies.

Read from the environment on every call, so a test's monkeypatch and a
runtime config change both take effect without re-importing the app.

- OAuth (Google/GitHub) is on when all four ``SSO_ENV`` client variables are
  set and off when none is; a partial set is refused at startup by
  ``api.main.lifespan``.
- The break-glass local admin is on when both ``LOCAL_ADMIN_ENV`` variables
  are set.

``SESSION_SIGNING_KEY`` belongs to neither: every sign-in method needs it, and
startup refuses a missing or dev key whenever any sign-in method is on.
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

LOCAL_ADMIN_ENV = ("DEFAULT_ADMIN_USERNAME", "DEFAULT_ADMIN_PASSWORD")


def sso_status() -> tuple[bool, list[str]]:
    """``(enabled, missing)``: enabled iff every OAuth client variable in ``SSO_ENV`` is set."""
    missing = [k for k in SSO_ENV if not os.environ.get(k)]
    return (not missing, missing)


def local_admin_enabled() -> bool:
    """True when both break-glass local-admin variables are set."""
    return all(os.environ.get(k) for k in LOCAL_ADMIN_ENV)


def sign_in_enabled() -> bool:
    """True when any sign-in method is on: OAuth or the break-glass local admin."""
    return sso_status()[0] or local_admin_enabled()


def configured_signing_key() -> str | None:
    """The operator's ``SESSION_SIGNING_KEY`` with surrounding whitespace removed,
    or None when nothing is left.

    The guard and every signer see this one normalised value, so a padded dev
    key (Make keeps the space before an inline ``# comment`` in ``.env``) is
    still recognised as the dev key. ``.env.example`` ships the variable
    empty, so a copied ``.env`` hands the process an empty string."""
    return os.environ.get(SIGNING_KEY_ENV, "").strip() or None


def session_signing_key() -> str:
    """The key every cookie signer and keyed digest uses: the configured one, else the dev placeholder."""
    return configured_signing_key() or DEV_SIGNING_KEY
