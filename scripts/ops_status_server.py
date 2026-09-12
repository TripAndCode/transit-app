#!/usr/bin/env python3
"""Authenticated HTTP front end for the combined operations-status document (item 123):
one HTML page, a compact text view, and a JSON endpoint, all gated behind the same
shared-secret check and all read-only -- every route is a `GET`, and none of them can
mutate anything (there is no write path in this app at all).

This is a small, standalone, VPS-local tool, not a router added to `api/`: `api/`
deploys to Railway (see `railway.json`), and nothing in this repo runs an HTTP server
on the VPS today (the VPS only runs `claude-loop.service`/`.timer` and cron jobs --
see `.claude/README.md`). Reusing `api/`'s session-cookie `require_admin` would need a
browser login against the production Postgres `users` table for a tool that has to work
from a bare `curl`/SSH context and has no DB dependency of its own; reusing
`api/routers/internal.py`'s shared-secret pattern instead (`OPS_STATUS_TOKEN`, fail
closed if unset -- mirrors that module's `CRON_SECRET`/`X-Cron-Secret` check) fits both
constraints. HTTP Basic (rather than a bespoke header) additionally lets a plain browser
tab authenticate against the HTML page without a login form.

Binds to localhost by default: reaching it from off-box is expected to go through an
SSH tunnel (`ssh -L 8642:127.0.0.1:8642 <vps>`) or an operator-configured reverse proxy,
never a bare public listener -- the Basic-auth gate is defense in depth on top of that
default, not a substitute for it. See `deploy/systemd/ops-status.service` for the
systemd unit template and install steps.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from scripts.ops_status_page import (
    DEFAULT_GITHUB_REPO_SLUG,
    DEFAULT_LOCAL_REPO,
    build_document,
    collect_all,
    render_html,
    render_text,
)

TOKEN_ENV_VAR = "OPS_STATUS_TOKEN"
HOST_ENV_VAR = "OPS_STATUS_HOST"
PORT_ENV_VAR = "OPS_STATUS_PORT"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8642

_basic_auth = HTTPBasic(auto_error=True)


def _require_token(credentials: HTTPBasicCredentials = Depends(_basic_auth)) -> None:
    """Fail closed (503) if the operator never configured a token -- a misconfigured
    deploy must never fall open to unauthenticated access -- then reject (401) any
    request whose password doesn't match, compared in constant time. The username is
    not itself secret (any value is accepted); only the password is checked, exactly
    like `api/routers/internal.py`'s single `X-Cron-Secret` value."""

    expected = os.environ.get(TOKEN_ENV_VAR)
    if not expected:
        raise HTTPException(status_code=503, detail=f"{TOKEN_ENV_VAR} not configured")
    if not hmac.compare_digest(credentials.password, expected):
        raise HTTPException(status_code=401, detail="Invalid credentials", headers={"WWW-Authenticate": "Basic"})


def _current_document() -> dict:
    documents = collect_all(local_repo=DEFAULT_LOCAL_REPO, github_repo_slug=DEFAULT_GITHUB_REPO_SLUG)
    return build_document(documents)


def create_app() -> FastAPI:
    # docs_url/redoc_url/openapi_url disabled: this app has exactly three read-only
    # routes, none of which need discovery tooling, and an OpenAPI schema is one more
    # surface that doesn't belong on an internal ops tool.
    app = FastAPI(title="transit-app ops status", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(_require_token)])
    async def status_page() -> str:
        return render_html(_current_document())

    @app.get("/status.txt", response_class=PlainTextResponse, dependencies=[Depends(_require_token)])
    async def status_text() -> str:
        return render_text(_current_document())

    @app.get("/status.json", dependencies=[Depends(_require_token)])
    async def status_json() -> JSONResponse:
        return JSONResponse(_current_document())

    return app


app = create_app()


def main() -> int:
    import uvicorn

    host = os.environ.get(HOST_ENV_VAR, DEFAULT_HOST)
    port = int(os.environ.get(PORT_ENV_VAR, str(DEFAULT_PORT)))
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
