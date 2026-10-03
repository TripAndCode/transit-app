"""``db.migrate._versions_on_disk`` is a pure function of on-disk migration
files, called on every board/ops poll (pipeline.health.migration_status).
It must not re-glob the directory on every call.
"""

import db.migrate as migrate


def test_versions_on_disk_is_cached_across_calls(tmp_path, monkeypatch):
    (tmp_path / "0001_a.up.sql").write_text("select 1;")
    (tmp_path / "0002_b.up.sql").write_text("select 1;")
    monkeypatch.setattr(migrate, "_MIGRATIONS_DIR", tmp_path)
    migrate.reset_for_tests()

    calls = []
    real_glob = type(tmp_path).glob

    def _counting_glob(self, pattern):
        calls.append(pattern)
        return real_glob(self, pattern)

    monkeypatch.setattr(type(tmp_path), "glob", _counting_glob)

    first = migrate._versions_on_disk()
    second = migrate._versions_on_disk()

    assert first == ["0001", "0002"]
    assert second == first
    assert len(calls) == 1, f"expected exactly one glob across two calls, got {len(calls)}"


def test_reset_for_tests_forces_a_fresh_read(tmp_path, monkeypatch):
    (tmp_path / "0001_a.up.sql").write_text("select 1;")
    monkeypatch.setattr(migrate, "_MIGRATIONS_DIR", tmp_path)
    migrate.reset_for_tests()

    assert migrate._versions_on_disk() == ["0001"]

    (tmp_path / "0002_b.up.sql").write_text("select 1;")
    # Without reset_for_tests, the stale cached list would still be ["0001"].
    migrate.reset_for_tests()
    assert migrate._versions_on_disk() == ["0001", "0002"]
