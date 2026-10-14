"""The agencies page's timetable reload and chained update, end to end.

Two halves:

- The endpoints, through ``api.main`` with a real session, the CSRF check and
  the test database: who may press, what a press records, and when a press is
  refused. The background runner is stubbed here -- it would download a
  timetable and run the sweep.
- The runner itself against the test database, with only the network fetch
  (the static strategy's ``fetch``) and the ingest/analyze work mocked, so
  the rows the page reads are the rows a real run writes.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import httpx
import psycopg2
import psycopg2.extras
import pytest
from httpx import ASGITransport

from api.admin_runs import LAST_STATIC_LOAD_SQL, LATEST_OPERATOR_RUNS_SQL, build_latest_runs
from api.middleware.ratelimit import limiter
from api.security import token_hash
from tests.conftest import TEST_ORIGIN, _test_pool

_ORIGIN = {"Origin": TEST_ORIGIN}


async def _seed_session(conn, *, role: str = "admin") -> tuple[str, int]:
    uid = (
        await conn.fetchrow(
            "INSERT INTO users (email, role) VALUES ($1, $2) RETURNING user_id",
            f"u{datetime.now().timestamp()}@x",
            role,
        )
    )["user_id"]
    sid = f"sid-{uid}-{datetime.now().timestamp()}"
    await conn.execute(
        "INSERT INTO sessions (sid_hash, user_id, expires_at) VALUES ($1, $2, $3)",
        token_hash(sid),
        uid,
        datetime.now(timezone.utc) + timedelta(days=1),
    )
    return sid, uid


async def _seed_agency(conn, *, static: bool = True, deleted: bool = False) -> int:
    return await conn.fetchval(
        "INSERT INTO agencies (agency_name, feed_url, static_url, static_strategy, deleted_at) "
        "VALUES ($1, $2, $3, $4, $5) RETURNING agency_id",
        "広島バス",
        f"https://example.test/{datetime.now().timestamp()}/feed.pb",
        "https://example.test/gtfs/current_data.zip" if static else None,
        "direct_url" if static else None,
        datetime.now(timezone.utc) if deleted else None,
    )


@pytest.fixture
async def app_client(apply_schema):
    from api.main import app

    limiter.reset()
    pool = await _test_pool()
    app.state.pool = pool
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url=TEST_ORIGIN) as c:
        yield c
    await pool.close()
    limiter.reset()


@pytest.fixture
def queued(monkeypatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("api.routers.internal._run_agency_update", lambda **kw: calls.append(kw))
    return calls


_PATHS = ["refresh-static", "update"]


# ── the endpoints ────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _PATHS)
async def test_a_member_who_is_not_an_admin_is_forbidden(app_client, aconn, queued, path):
    sid, _ = await _seed_session(aconn, role="user")
    aid = await _seed_agency(aconn)
    r = await app_client.post(f"/api/admin/agencies/{aid}/{path}", cookies={"sid": sid}, headers=_ORIGIN)
    assert r.status_code == 403
    assert queued == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _PATHS)
async def test_a_press_without_a_same_site_origin_is_refused(app_client, aconn, queued, path):
    sid, _ = await _seed_session(aconn)
    aid = await _seed_agency(aconn)
    r = await app_client.post(f"/api/admin/agencies/{aid}/{path}", cookies={"sid": sid})
    assert r.status_code == 403
    assert await aconn.fetchval("SELECT count(*) FROM pipeline_runs") == 0
    assert queued == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "kind"), [("refresh-static", "static"), ("update", "update")])
async def test_a_press_opens_an_umbrella_run_audits_it_and_answers_202(app_client, aconn, queued, path, kind):
    sid, uid = await _seed_session(aconn)
    aid = await _seed_agency(aconn)
    r = await app_client.post(f"/api/admin/agencies/{aid}/{path}", cookies={"sid": sid}, headers=_ORIGIN)

    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    row = await aconn.fetchrow("SELECT * FROM pipeline_runs WHERE run_id = $1", run_id)
    assert (row["kind"], row["agency_id"], row["status"], row["requested_by"], row["parent_run_id"]) == (
        kind,
        aid,
        "running",
        uid,
        None,
    )
    audit = await aconn.fetchval("SELECT action FROM admin_audit WHERE target_id = $1", str(aid))
    assert audit in {"agency.static_refresh_requested", "agency.update_requested"}
    assert queued == [
        {"agency_id": aid, "requested_by": uid, "run_id": run_id, "then_ingest_and_analyze": kind == "update"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _PATHS)
async def test_a_second_press_while_the_first_run_is_in_flight_is_409(app_client, aconn, queued, path):
    sid, _ = await _seed_session(aconn)
    aid = await _seed_agency(aconn)
    first = await app_client.post(f"/api/admin/agencies/{aid}/update", cookies={"sid": sid}, headers=_ORIGIN)
    assert first.status_code == 202
    second = await app_client.post(f"/api/admin/agencies/{aid}/{path}", cookies={"sid": sid}, headers=_ORIGIN)
    assert second.status_code == 409
    assert await aconn.fetchval("SELECT count(*) FROM pipeline_runs WHERE agency_id = $1", aid) == 1
    reanalyze = await app_client.post(f"/api/admin/agencies/{aid}/reanalyze", cookies={"sid": sid}, headers=_ORIGIN)
    assert reanalyze.status_code == 409


@pytest.mark.asyncio
async def test_a_run_abandoned_past_the_reap_age_does_not_block_a_new_one(app_client, aconn, queued):
    sid, uid = await _seed_session(aconn)
    aid = await _seed_agency(aconn)
    await aconn.execute(
        "INSERT INTO pipeline_runs (kind, agency_id, started_at, status, requested_by) "
        "VALUES ('update', $1, now() - interval '3 hours', 'running', $2)",
        aid,
        uid,
    )
    r = await app_client.post(f"/api/admin/agencies/{aid}/update", cookies={"sid": sid}, headers=_ORIGIN)
    assert r.status_code == 202


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _PATHS)
async def test_a_disabled_agency_is_409(app_client, aconn, queued, path):
    sid, _ = await _seed_session(aconn)
    aid = await _seed_agency(aconn, deleted=True)
    r = await app_client.post(f"/api/admin/agencies/{aid}/{path}", cookies={"sid": sid}, headers=_ORIGIN)
    assert r.status_code == 409
    assert queued == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _PATHS)
async def test_an_agency_with_no_timetable_source_is_409(app_client, aconn, queued, path):
    sid, _ = await _seed_session(aconn)
    aid = await _seed_agency(aconn, static=False)
    r = await app_client.post(f"/api/admin/agencies/{aid}/{path}", cookies={"sid": sid}, headers=_ORIGIN)
    assert r.status_code == 409
    assert queued == []


@pytest.mark.asyncio
async def test_health_reports_each_agencys_latest_run_step_by_step(app_client, aconn):
    sid, uid = await _seed_session(aconn)
    aid = await _seed_agency(aconn)
    umbrella = await aconn.fetchval(
        "INSERT INTO pipeline_runs (kind, agency_id, started_at, status, requested_by) "
        "VALUES ('update', $1, now() - interval '3 minutes', 'running', $2) RETURNING run_id",
        aid,
        uid,
    )
    await aconn.execute(
        "INSERT INTO pipeline_runs "
        "(kind, agency_id, started_at, finished_at, status, rows, requested_by, parent_run_id) "
        "VALUES ('static', $1, now() - interval '3 minutes', now() - interval '2 minutes', 'ok', 27, $2, $3)",
        aid,
        uid,
        umbrella,
    )
    await aconn.execute(
        "INSERT INTO pipeline_runs (kind, agency_id, started_at, status, requested_by, parent_run_id) "
        "VALUES ('ingest', $1, now() - interval '2 minutes', 'running', $2, $3)",
        aid,
        uid,
        umbrella,
    )
    # A cron sweep's row for the same agency is not part of the operator's run.
    await aconn.execute(
        "INSERT INTO pipeline_runs (kind, agency_id, started_at, finished_at, status) "
        "VALUES ('analyze', $1, now() - interval '1 minute', now(), 'ok')",
        aid,
    )

    r = await app_client.get("/api/admin/agencies/health", cookies={"sid": sid})
    assert r.status_code == 200, r.text
    row = next(h for h in r.json() if h["agency_id"] == aid)
    assert row["in_flight"] is True
    static_finished = datetime.now(timezone.utc) - timedelta(minutes=2)
    assert row["static_loaded_on"] == static_finished.astimezone(timezone(timedelta(hours=9))).date().isoformat()
    run = row["latest_run"]
    assert (run["run_id"], run["kind"], run["status"]) == (umbrella, "update", "running")
    assert [(s["step"], s["status"], s["rows"]) for s in run["steps"]] == [
        ("static", "ok", 27),
        ("ingest", "running", None),
        ("analyze", "pending", None),
    ]


@pytest.mark.asyncio
async def test_health_carries_no_run_for_an_agency_no_operator_has_run(app_client, aconn):
    sid, _ = await _seed_session(aconn)
    aid = await _seed_agency(aconn, static=False)
    r = await app_client.get("/api/admin/agencies/health", cookies={"sid": sid})
    row = next(h for h in r.json() if h["agency_id"] == aid)
    assert row["latest_run"] is None
    assert row["in_flight"] is False
    assert row["static_loaded_on"] is None


# ── the runner ───────────────────────────────────────────────────────────


@pytest.fixture
def runner_env(pg_conn, monkeypatch, tmp_path):
    """An agency, an admin, and an umbrella run opened the way the endpoint
    opens it; the runner reads `DATABASE_URL` from the environment."""
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    monkeypatch.setattr("pipeline.static_fetcher.DEFAULT_STATIC_DEST", str(tmp_path / "static"))
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO users (email, role) VALUES ('runner@x', 'admin') RETURNING user_id")
        uid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, static_url, static_strategy) "
            "VALUES ('広島バス', 'https://example.test/feed.pb', 'https://example.test/gtfs/current_data.zip', "
            "'direct_url') RETURNING agency_id"
        )
        aid = cur.fetchone()[0]
    pg_conn.commit()

    def open_run(kind: str) -> int:
        with pg_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pipeline_runs (kind, agency_id, started_at, status, requested_by) "
                "VALUES (%s, %s, now(), 'running', %s) RETURNING run_id",
                (kind, aid, uid),
            )
            run_id = cur.fetchone()[0]
        pg_conn.commit()
        return run_id

    return {"agency_id": aid, "user_id": uid, "open_run": open_run, "tmp": tmp_path}


def _gtfs_zip(path: Path, routes: int) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("stops.txt", "stop_id,stop_name,stop_lat,stop_lon\nS1,Stop 1,34.39,132.45\n")
        zf.writestr(
            "routes.txt",
            "route_id,route_short_name,route_long_name\n" + "".join(f"R{i},{i},Route {i}\n" for i in range(routes)),
        )
        zf.writestr("trips.txt", "trip_id,route_id,service_id\nT1,R0,WK\n")
    return path


def _seed_timetable(pg_conn, agency_id: int) -> None:
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO static_stops (agency_id, stop_id, stop_name) VALUES (%s, 'S1', 'Stop')", (agency_id,))
        cur.execute("INSERT INTO static_routes (agency_id, route_id) VALUES (%s, 'R1')", (agency_id,))
    pg_conn.commit()


def _upstream_answers(code: int):
    """Every request the timetable source receives is answered with ``code``.

    Patches ``direct_url.safe_urlopen`` itself, not ``url_guard._opener``, so
    the URL guard's DNS check never runs against the test host, which does
    not resolve."""

    def _open(req, **_kwargs):
        raise HTTPError(req.full_url, code, "status", {}, None)  # type: ignore[arg-type]

    return patch("pipeline.strategies.direct_url.safe_urlopen", side_effect=_open)


def _upstream_serving(body: bytes, etag: str):
    """The timetable source serves ``body`` with ``etag``, answering 304 to a
    request that already holds that tag; it publishes no latest.zip."""

    def _open(req, **_kwargs):
        if req.full_url.endswith("/latest.zip"):
            raise HTTPError(req.full_url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
        if req.get_header("If-none-match") == etag:
            raise HTTPError(req.full_url, 304, "Not Modified", {}, None)  # type: ignore[arg-type]
        resp = MagicMock()
        resp.read.return_value = body
        resp.headers = {"ETag": etag}
        resp.__enter__.return_value = resp
        return resp

    return patch("pipeline.strategies.direct_url.safe_urlopen", side_effect=_open)


def _rows(pg_conn, run_id: int) -> list[tuple[Any, ...]]:
    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT run_id, kind, status, rows, error, parent_run_id FROM pipeline_runs "
            "WHERE run_id = %s OR parent_run_id = %s ORDER BY run_id",
            (run_id, run_id),
        )
        return cur.fetchall()


def test_a_failed_timetable_step_stops_the_chain_with_its_error_recorded(runner_env, pg_conn):
    from api.routers.internal import _run_agency_update

    run_id = runner_env["open_run"]("update")

    def _fetch(agency_id, static_url, dest_dir, *, force=False):
        raise OSError("connection reset fetching https://example.test/gtfs/current_data.zip?key=SECRET")

    with (
        patch("pipeline.strategies.direct_url.fetch", side_effect=_fetch),
        patch("api.routers.internal._ingest_and_analyze_sweep") as sweep,
    ):
        _run_agency_update(
            agency_id=runner_env["agency_id"],
            requested_by=runner_env["user_id"],
            run_id=run_id,
            then_ingest_and_analyze=True,
        )

    sweep.assert_not_called()
    (umbrella, step) = _rows(pg_conn, run_id)
    assert umbrella[1:3] == ("update", "error")
    assert step[1:3] == ("static", "error")
    assert step[5] == run_id
    assert "connection reset" in step[4]
    # A static URL routinely carries an API key; the row every admin reads
    # must not.
    assert "SECRET" not in step[4] and "SECRET" not in umbrella[4]


@pytest.mark.parametrize("chained", [True, False])
def test_a_timetable_source_that_fails_is_an_error_even_with_a_timetable_loaded(runner_env, pg_conn, chained):
    """A dead source must not read as "no newer timetable" for an agency that
    already has one: the step closes `error` with the reason the run card
    shows, and the chain stops there."""
    from api.routers.internal import _run_agency_update

    _seed_timetable(pg_conn, runner_env["agency_id"])
    run_id = runner_env["open_run"]("update" if chained else "static")
    with _upstream_answers(404), patch("api.routers.internal._ingest_and_analyze_sweep") as sweep:
        _run_agency_update(
            agency_id=runner_env["agency_id"],
            requested_by=runner_env["user_id"],
            run_id=run_id,
            then_ingest_and_analyze=chained,
        )

    sweep.assert_not_called()
    (umbrella, step) = _rows(pg_conn, run_id)
    assert (umbrella[2], step[1], step[2]) == ("error", "static", "error")
    assert "HTTP 404" in step[4] and "current_data.zip" in step[4]
    assert "HTTP 404" in umbrella[4]

    with pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(LATEST_OPERATOR_RUNS_SQL)
        card = build_latest_runs(cur.fetchall())[runner_env["agency_id"]]
    assert card["steps"][0]["status"] == "error"
    assert "HTTP 404" in card["steps"][0]["error"]


def test_a_source_that_answers_not_modified_closes_the_step_unchanged(runner_env, pg_conn):
    from api.routers.internal import _run_agency_update

    _seed_timetable(pg_conn, runner_env["agency_id"])
    run_id = runner_env["open_run"]("static")
    with _upstream_answers(304):
        _run_agency_update(
            agency_id=runner_env["agency_id"],
            requested_by=runner_env["user_id"],
            run_id=run_id,
            then_ingest_and_analyze=False,
        )

    (umbrella, step) = _rows(pg_conn, run_id)
    assert (umbrella[2], step[2], step[4]) == ("unchanged", "unchanged", None)


def test_an_agency_with_no_timetable_reloads_the_zip_its_manifest_calls_unchanged(runner_env, pg_conn):
    """The change manifest on disk can outlive the timetable tables, so a
    source with nothing newer than the last zip that loaded can still leave
    the agency with no timetable at all. The reload then fetches past the
    manifest, the chain goes on with a timetable, and the next reload asks
    the source for changes again."""
    import pipeline.static_fetcher as static_fetcher
    from api.routers.internal import SweepOutcome, _run_agency_update

    aid = runner_env["agency_id"]
    body = _gtfs_zip(runner_env["tmp"] / "upstream.zip", routes=2).read_bytes()
    agency_dir = Path(static_fetcher.DEFAULT_STATIC_DEST) / str(aid)
    agency_dir.mkdir(parents=True)
    loaded = {"sha256": hashlib.sha256(body).hexdigest(), "etag": "et1"}
    (agency_dir / "_manifest.json").write_text(json.dumps({"current": loaded, "latest": loaded}))

    run_id = runner_env["open_run"]("update")
    with (
        _upstream_serving(body, "et1"),
        patch("api.routers.internal._ingest_and_analyze_sweep", return_value=SweepOutcome("ok")) as sweep,
    ):
        _run_agency_update(
            agency_id=aid,
            requested_by=runner_env["user_id"],
            run_id=run_id,
            then_ingest_and_analyze=True,
        )

    sweep.assert_called_once()
    (umbrella, step) = _rows(pg_conn, run_id)
    assert step[1:4] == ("static", "ok", 2)
    assert umbrella[2] == "ok"

    again = runner_env["open_run"]("static")
    with _upstream_serving(body, "et1"):
        _run_agency_update(
            agency_id=aid,
            requested_by=runner_env["user_id"],
            run_id=again,
            then_ingest_and_analyze=False,
        )
    (umbrella, step) = _rows(pg_conn, again)
    assert (umbrella[2], step[2]) == ("unchanged", "unchanged")


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("answers 304", "the source sent no timetable, though this agency has none loaded"),
        ("cleared", "this agency has no timetable URL or fetch method configured"),
    ],
)
def test_a_reload_that_leaves_an_agency_with_no_timetable_fails_and_stops_the_chain(
    runner_env, pg_conn, source, reason
):
    """A source that sends nothing even when asked without validators, or one
    cleared after the press was accepted, leaves the agency with no timetable;
    the chain must not go on to ingest against an empty schedule."""
    from api.routers.internal import _run_agency_update

    if source == "cleared":
        with pg_conn.cursor() as cur:
            cur.execute("UPDATE agencies SET static_url = NULL WHERE agency_id = %s", (runner_env["agency_id"],))
        pg_conn.commit()
    run_id = runner_env["open_run"]("update")
    with _upstream_answers(304), patch("api.routers.internal._ingest_and_analyze_sweep") as sweep:
        _run_agency_update(
            agency_id=runner_env["agency_id"],
            requested_by=runner_env["user_id"],
            run_id=run_id,
            then_ingest_and_analyze=True,
        )

    sweep.assert_not_called()
    (umbrella, step) = _rows(pg_conn, run_id)
    assert (umbrella[2], step[1], step[2]) == ("error", "static", "error")
    assert reason in step[4]
    assert reason in umbrella[4]


def test_a_loaded_timetable_records_its_route_count_and_hands_over_to_the_sweep(runner_env, pg_conn):
    from api.routers.internal import SweepOutcome, _run_agency_update

    run_id = runner_env["open_run"]("update")
    zip_path = _gtfs_zip(runner_env["tmp"] / "gtfs_static_20261013.zip", routes=3)
    with (
        patch("pipeline.strategies.direct_url.fetch", return_value=zip_path),
        patch("api.routers.internal._ingest_and_analyze_sweep", return_value=SweepOutcome("ok")) as sweep,
    ):
        _run_agency_update(
            agency_id=runner_env["agency_id"],
            requested_by=runner_env["user_id"],
            run_id=run_id,
            then_ingest_and_analyze=True,
        )

    (umbrella, step) = _rows(pg_conn, run_id)
    assert step[1:4] == ("static", "ok", 3)
    assert umbrella[2] == "ok"
    kwargs = sweep.call_args.kwargs
    assert kwargs["agency_ids"] == [runner_env["agency_id"]]
    assert kwargs["run_id"] == run_id
    assert kwargs["run_weather"] is False
    assert kwargs["stop_on_failure"] is True


def test_no_newer_timetable_closes_the_step_unchanged_and_moves_no_load_date(runner_env, pg_conn):
    """A check that loaded nothing is not a load: recording it `ok` would let
    "last loaded" claim today for a timetable that may be months old."""
    from api.routers.internal import _run_agency_update

    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO static_stops (agency_id, stop_id, stop_name) VALUES (%s, 'S1', 'Stop')",
            (runner_env["agency_id"],),
        )
        cur.execute(
            "INSERT INTO static_routes (agency_id, route_id) VALUES (%s, 'R1'), (%s, 'R2')",
            (runner_env["agency_id"], runner_env["agency_id"]),
        )
    pg_conn.commit()
    run_id = runner_env["open_run"]("static")
    with (
        patch("pipeline.strategies.direct_url.fetch", return_value=None),
        patch("api.routers.internal._ingest_and_analyze_sweep") as sweep,
    ):
        _run_agency_update(
            agency_id=runner_env["agency_id"],
            requested_by=runner_env["user_id"],
            run_id=run_id,
            then_ingest_and_analyze=False,
        )

    sweep.assert_not_called()
    (umbrella, step) = _rows(pg_conn, run_id)
    assert step[1:4] == ("static", "unchanged", None)
    # The reload-only umbrella closes with its step's outcome: the board
    # draws both rows of one no-op check the same way.
    assert umbrella[1:3] == ("static", "unchanged")
    with pg_conn.cursor() as cur:
        cur.execute(LAST_STATIC_LOAD_SQL)
        assert cur.fetchall() == []


def test_a_reload_on_its_own_that_loaded_a_zip_closes_its_umbrella_ok(runner_env, pg_conn):
    from api.routers.internal import _run_agency_update

    run_id = runner_env["open_run"]("static")
    zip_path = _gtfs_zip(runner_env["tmp"] / "gtfs_static_20261013.zip", routes=2)
    with (
        patch("pipeline.strategies.direct_url.fetch", return_value=zip_path),
        patch("api.routers.internal._ingest_and_analyze_sweep") as sweep,
    ):
        _run_agency_update(
            agency_id=runner_env["agency_id"],
            requested_by=runner_env["user_id"],
            run_id=run_id,
            then_ingest_and_analyze=False,
        )

    sweep.assert_not_called()
    (umbrella, step) = _rows(pg_conn, run_id)
    assert step[1:4] == ("static", "ok", 2)
    assert umbrella[1:3] == ("static", "ok")


def test_the_chained_sweep_stops_an_agency_at_its_first_failed_stage(runner_env, pg_conn):
    """A reanalyze keeps going after a failed ingest -- re-aggregating what is
    already stored is still worth doing there. The chained update says it
    stops at the first failure, so it asks the sweep to."""
    from api.routers.internal import _run_ingest_and_analyze

    run_id = runner_env["open_run"]("update")
    with (
        patch("pipeline.ingest.ingest_live", side_effect=RuntimeError("feed down")),
        patch("pipeline.promote.promote_closed_days") as promote,
        patch("pipeline.analyze.analyze") as analyze,
        patch("pipeline.freshness.check_agg_freshness", return_value=[]),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
    ):
        _run_ingest_and_analyze(
            agency_ids=[runner_env["agency_id"]],
            requested_by=runner_env["user_id"],
            run_weather=False,
            run_id=run_id,
            stop_on_failure=True,
        )

    promote.assert_not_called()
    analyze.assert_not_called()
    rows = _rows(pg_conn, run_id)
    assert [(r[1], r[2]) for r in rows] == [("update", "error"), ("ingest", "error")]
    assert rows[1][5] == run_id


def test_the_sweep_files_its_stages_under_the_operators_run(runner_env, pg_conn):
    from api.routers.internal import _run_ingest_and_analyze

    run_id = runner_env["open_run"]("ingest")
    with (
        patch("pipeline.ingest.ingest_live", return_value=5),
        patch("pipeline.promote.promote_closed_days", return_value=4),
        patch("pipeline.analyze.analyze"),
        patch("pipeline.freshness.check_agg_freshness", return_value=[]),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
    ):
        _run_ingest_and_analyze(
            agency_ids=[runner_env["agency_id"]],
            requested_by=runner_env["user_id"],
            run_weather=False,
            run_id=run_id,
        )

    rows = _rows(pg_conn, run_id)
    assert [(r[1], r[2], r[5]) for r in rows] == [
        ("ingest", "ok", None),
        ("ingest", "ok", run_id),
        ("promote", "ok", run_id),
        ("analyze", "ok", run_id),
    ]


def test_a_cron_sweep_files_its_stages_under_nothing(runner_env, pg_conn):
    from api.routers.internal import _run_ingest_and_analyze

    with (
        patch("pipeline.ingest.ingest_live", return_value=0),
        patch("pipeline.promote.promote_closed_days", return_value=0),
        patch("pipeline.analyze.analyze"),
        patch("pipeline.freshness.check_agg_freshness", return_value=[]),
        patch("pipeline.clickhouse.get_client", return_value=MagicMock()),
        patch("api.routers.internal._run_weather_ingest"),
        patch("api.routers.internal._run_ml_forecast_ingest"),
    ):
        _run_ingest_and_analyze()

    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM pipeline_runs WHERE parent_run_id IS NOT NULL")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM pipeline_runs WHERE agency_id = %s", (runner_env["agency_id"],))
        assert cur.fetchone()[0] == 3


def test_the_command_line_reload_records_a_static_run(runner_env, pg_conn, monkeypatch):
    """The timetable column's "last loaded" date reads the latest successful
    `static` run, so a reload from the terminal has to leave one too."""
    import argparse

    import gtfs_pipeline

    zip_path = _gtfs_zip(runner_env["tmp"] / "gtfs_static_20261013.zip", routes=1)
    monkeypatch.setattr(gtfs_pipeline, "_get_conn", lambda: psycopg2.connect(os.environ["DATABASE_URL"]))
    with patch("pipeline.strategies.direct_url.fetch", return_value=zip_path):
        gtfs_pipeline.cmd_refresh_static(
            argparse.Namespace(agency_id=str(runner_env["agency_id"]), dest=str(runner_env["tmp"]), force=False)
        )

    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute("SELECT kind, status FROM pipeline_runs WHERE agency_id = %s", (runner_env["agency_id"],))
        assert cur.fetchall() == [("static", "ok")]


def test_the_command_line_reload_of_one_agency_fails_when_its_source_fails(runner_env, pg_conn, monkeypatch):
    """The exception propagates, so the command exits nonzero, and the run
    it recorded says why."""
    import argparse

    import gtfs_pipeline
    from pipeline.strategies.errors import StaticFetchError

    _seed_timetable(pg_conn, runner_env["agency_id"])
    monkeypatch.setattr(gtfs_pipeline, "_get_conn", lambda: psycopg2.connect(os.environ["DATABASE_URL"]))
    with _upstream_answers(500), pytest.raises(StaticFetchError):
        gtfs_pipeline.cmd_refresh_static(
            argparse.Namespace(agency_id=str(runner_env["agency_id"]), dest=str(runner_env["tmp"]), force=False)
        )

    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute("SELECT kind, status, error FROM pipeline_runs WHERE agency_id = %s", (runner_env["agency_id"],))
        ((kind, status, error),) = cur.fetchall()
    assert (kind, status) == ("static", "error")
    assert "HTTP 500" in error


def test_the_command_line_reload_of_every_agency_reports_a_failed_source(runner_env, pg_conn, caplog):
    from pipeline.static_fetcher import refresh_all
    from pipeline.strategies.errors import StaticFetchError

    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, static_url, static_strategy) "
            "VALUES ('second', 'https://example.test/2/feed.pb', 'https://example.test/2/gtfs.zip', 'direct_url') "
            "RETURNING agency_id"
        )
        second = cur.fetchone()[0]
    pg_conn.commit()
    zip_path = _gtfs_zip(runner_env["tmp"] / "gtfs_static_20261013.zip", routes=2)

    def _fetch(agency_id, static_url, dest_dir, *, force=False):
        if agency_id == runner_env["agency_id"]:
            raise StaticFetchError("HTTP 404 Not Found from https://example.test/gtfs/current_data.zip")
        return zip_path

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with patch("pipeline.strategies.direct_url.fetch", side_effect=_fetch):
            assert refresh_all(conn, runner_env["tmp"]) == (1, 2, [runner_env["agency_id"]])
    finally:
        conn.close()

    assert "HTTP 404 Not Found" in caplog.text
    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute("SELECT agency_id, status, rows, error FROM pipeline_runs ORDER BY agency_id")
        failed, loaded = cur.fetchall()
    assert failed[:3] == (runner_env["agency_id"], "error", None)
    assert "HTTP 404 Not Found" in failed[3]
    assert loaded == (second, "ok", 2, None)


def test_the_command_line_reload_of_every_agency_records_one_static_run_each(runner_env, pg_conn):
    """`refresh-static` without `--agency-id` goes through `refresh_all`,
    which records each agency's reload the same way."""
    from pipeline.static_fetcher import refresh_all

    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, static_url, static_strategy) "
            "VALUES ('second', 'https://example.test/2/feed.pb', 'https://example.test/2/gtfs.zip', 'direct_url') "
            "RETURNING agency_id"
        )
        second = cur.fetchone()[0]
    pg_conn.commit()
    _seed_timetable(pg_conn, second)
    zip_path = _gtfs_zip(runner_env["tmp"] / "gtfs_static_20261013.zip", routes=2)

    def _fetch(agency_id, static_url, dest_dir, *, force=False):
        return zip_path if agency_id == runner_env["agency_id"] else None

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with patch("pipeline.strategies.direct_url.fetch", side_effect=_fetch):
            assert refresh_all(conn, runner_env["tmp"]) == (1, 2, [])
    finally:
        conn.close()

    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute("SELECT agency_id, kind, status, rows FROM pipeline_runs ORDER BY agency_id")
        assert cur.fetchall() == [
            (runner_env["agency_id"], "static", "ok", 2),
            (second, "static", "unchanged", None),
        ]
