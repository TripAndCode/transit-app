"""Whether Google/GitHub sign-in is configured for this process.

Read from the environment on every call, so a test's monkeypatch and a
runtime config change both take effect without re-importing the app. All
five variables set means SSO is on; none set is anonymous-only mode; a
partial set is refused at startup by ``api.main.lifespan``.
"""

import os

SSO_ENV = (
    "SESSION_SIGNING_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
)


def sso_status() -> tuple[bool, list[str]]:
    """``(enabled, missing)``: enabled iff every variable in ``SSO_ENV`` is set."""
    missing = [k for k in SSO_ENV if not os.environ.get(k)]
    return (not missing, missing)
