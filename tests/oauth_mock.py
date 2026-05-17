"""Minimal OAuth provider mock used by test_oauth_flow.

Implements just enough of /authorize, /token, and userinfo for Authlib.
Driven by `MockProvider.scenarios` — set fixed code/token/userinfo before
running the test.
"""

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode


@dataclass
class MockProvider:
    code_to_userinfo: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_redirect_uri: str | None = None
    last_state: str | None = None
    last_code_challenge: str | None = None
    last_code_verifier: str | None = None

    def authorize_url(self, params: dict[str, str]) -> str:
        self.last_redirect_uri = params.get("redirect_uri")
        self.last_state = params.get("state")
        self.last_code_challenge = params.get("code_challenge")
        return f"https://mock-provider/authorize?{urlencode(params)}"

    def exchange(self, body: bytes) -> dict[str, Any]:
        form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
        self.last_code_verifier = form.get("code_verifier")
        code = form.get("code")
        if code not in self.code_to_userinfo:
            return {"error": "invalid_grant"}
        return {
            "access_token": f"tok-{code}",
            "token_type": "Bearer",
            "id_token": "fake-id-token",
            "scope": "openid email profile",
        }

    def userinfo(self, code: str) -> dict[str, Any]:
        return self.code_to_userinfo.get(code, {})
