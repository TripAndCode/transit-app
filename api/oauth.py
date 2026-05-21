"""Authlib OAuth client registry. Two providers: Google (OIDC) and GitHub.

Provider config reads env at module-import time so tests can override
via monkeypatching environment + reimporting. Production call sites
import `oauth` and call `oauth.create_client(provider)`.
"""

import os

from authlib.integrations.starlette_client import OAuth

oauth = OAuth()

oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
)

oauth.register(
    name="github",
    client_id=os.environ.get("GITHUB_CLIENT_ID", ""),
    client_secret=os.environ.get("GITHUB_CLIENT_SECRET", ""),
    api_base_url="https://api.github.com/",
    access_token_url="https://github.com/login/oauth/access_token",
    authorize_url="https://github.com/login/oauth/authorize",
    client_kwargs={"scope": "read:user user:email", "code_challenge_method": "S256"},
)
