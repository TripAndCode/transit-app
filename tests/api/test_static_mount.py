"""Tests for the SPA static mount in ``api.main``.

Verifies that with a built ``api/static/`` directory present:

* ``/`` and unknown SPA paths return ``index.html``
* ``/assets/*`` is served from the static assets dir
* ``/health`` is still routed to the FastAPI handler (not swallowed by the SPA)
* Unknown paths under API prefixes (``/api/...``, ``/agencies``, etc.) return a
  structured JSON 404 so frontend fetches see real errors, not HTML

The fixture writes a tiny fake build into a tmp dir, monkeypatches
``api.main.STATIC_DIR``, re-runs ``_maybe_mount_static``, and cleans up the
mutated ``app.routes`` list on teardown so other test modules see a clean app.
"""

import pytest
from fastapi.testclient import TestClient

from api.main import app


# Override conftest's session-scoped autouse `apply_schema` for this module only.
# conftest.apply_schema does psycopg2.connect(DATABASE_URL); these tests don't
# touch the DB, so we skip it (avoids needing the Postgres container running).
@pytest.fixture(scope="session", autouse=True)
def apply_schema():
    """No-op override of conftest.apply_schema; this module needs no database."""
    return


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient backed by a temp static dir that mimics a real Vite build.

    Avoids ``with TestClient(app)`` so the lifespan (which needs ``DATABASE_URL``
    and ``GROQ_API_KEY``) is not triggered. Cleans up SPA routes on teardown.
    """
    static_dir = tmp_path / "static"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text("<html><body>SPA</body></html>")
    (static_dir / "assets" / "app.js").write_text("console.log('app');")
    # Mimics Vite's public/ dir, which is copied verbatim to dist/'s *root*
    # (not under assets/) -- e.g. frontend/public/user-manual/*.
    (static_dir / "user-manual").mkdir(parents=True)
    (static_dir / "user-manual" / "en.md").write_text("# Manual\n\nReal manual text.")
    # A file outside static_dir, for the path-traversal test below.
    (tmp_path / "outside-secret.txt").write_text("should never be served")

    monkeypatch.setattr("api.main.STATIC_DIR", str(static_dir))
    from api.main import _maybe_mount_static

    _maybe_mount_static(app)

    yield TestClient(app)

    app.routes[:] = [r for r in app.routes if getattr(r, "name", None) not in ("spa_fallback", "assets")]


def test_root_returns_spa_index(client):
    """``/`` falls back to ``index.html``."""
    r = client.get("/")
    assert r.status_code == 200
    assert "SPA" in r.text


def test_unknown_path_falls_back_to_index(client):
    """Unknown SPA paths (e.g. ``/agencies/1/map``) return the SPA shell."""
    r = client.get("/agencies/1/map")
    assert r.status_code == 200
    assert "SPA" in r.text


def test_assets_served(client):
    """``/assets/*`` serves files from the built static asset directory."""
    r = client.get("/assets/app.js")
    assert r.status_code == 200
    assert "console.log" in r.text


def test_health_still_routed_to_api(client):
    """The ``/health`` API route is not shadowed by the SPA fallback."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_root_level_public_file_served_not_swallowed_by_spa(client):
    """Files Vite's public/ dir copies to dist/'s root (e.g. the user-manual
    markdown) must be served as themselves, not silently replaced by the SPA
    shell -- see api/main.py's spa_fallback docstring for why this needs an
    explicit on-disk-file check before falling back to index.html."""
    r = client.get("/user-manual/en.md")
    assert r.status_code == 200
    assert "Real manual text." in r.text
    assert "SPA" not in r.text


def test_path_traversal_cannot_escape_static_root(client):
    """A crafted path can't read a file that lives outside STATIC_DIR."""
    r = client.get("/../outside-secret.txt")
    assert "should never be served" not in r.text


def test_unknown_api_path_returns_json_404(client):
    """Unknown ``/api/*`` paths return JSON 404, not the SPA HTML.

    Without the API-prefix guard, frontend fetches against a typo'd or removed
    endpoint would get HTTP 200 + ``index.html``, then fail downstream when
    ``r.json()`` chokes on HTML — masking the real 404 as a parse error.

    Use a path that does not match any registered router pattern (extra
    segment under ``/api/{agency_id}``) so route resolution falls through to
    the SPA fallback rather than hitting a real handler.
    """
    r = client.get("/api/1/no_such_endpoint")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"detail": "Not Found"}
