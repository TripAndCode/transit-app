import pytest
from fastapi.testclient import TestClient

from api.main import app


# Override conftest's session-scoped autouse `apply_schema` for this module only.
# conftest.apply_schema does psycopg2.connect(DATABASE_URL); these tests don't
# touch the DB, so we skip it (avoids needing the Postgres container running).
@pytest.fixture(scope="session", autouse=True)
def apply_schema():
    return


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient using a temp static dir populated like a real Vite build.

    Avoids using `with TestClient(app)` so the lifespan (which needs
    DATABASE_URL + GROQ_API_KEY) is not triggered.
    """
    static_dir = tmp_path / "static"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text("<html><body>SPA</body></html>")
    (static_dir / "assets" / "app.js").write_text("console.log('app');")

    monkeypatch.setattr("api.main.STATIC_DIR", str(static_dir))
    from api.main import _maybe_mount_static
    _maybe_mount_static(app)

    yield TestClient(app)

    # Teardown: strip the SPA routes so other test modules start with a clean app.
    # monkeypatch restores STATIC_DIR but not the mutated app.routes list.
    app.routes[:] = [
        r for r in app.routes
        if getattr(r, "name", None) not in ("spa_fallback", "assets")
    ]


def test_root_returns_spa_index(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "SPA" in r.text


def test_unknown_path_falls_back_to_index(client):
    r = client.get("/agencies/1/map")
    assert r.status_code == 200
    assert "SPA" in r.text


def test_assets_served(client):
    r = client.get("/assets/app.js")
    assert r.status_code == 200
    assert "console.log" in r.text


def test_health_still_routed_to_api(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
