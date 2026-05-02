import pytest
from fastapi.testclient import TestClient

from api.main import app


# Override the session-scoped autouse fixture from conftest.py so this module
# does not attempt a real PostgreSQL connection (Docker is not running locally).
@pytest.fixture(scope="session", autouse=True)
def apply_schema():
    """No-op override: static mount tests don't need a database."""
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

    return TestClient(app)


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
