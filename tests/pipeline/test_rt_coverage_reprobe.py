"""The unattended coverage re-probe against a real registry and run table.

The selection rules and the job's control flow are covered without a
database in `tests/unit/test_rt_coverage_reprobe.py`. These tests check what
actually lands in `rt_field_coverage_probes` and `pipeline_runs`, and that
the cron sweep's own entry point reaches it. The feed fetch is always
stubbed: a checked-in capture stands in for a live poll.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

from pipeline import rt_coverage_reprobe as reprobe
from pipeline.strategies.static_join import RT_COVERAGE_FIELDS, field_coverage
from tests.conftest import _database_url
from tests.fixtures.clock import freeze_now
from tests.fixtures.gtfs_rt import header_only_feed, stop_time_update, trip_update, trip_update_feed

JST = timezone(timedelta(hours=9))
# Inside every agency's service window, whatever the wall clock says.
MORNING = datetime(2026, 10, 27, 10, 0, tzinfo=JST)
FEED_URL = "https://feeds.example.jp/realtime/9/trip_updates.bin"
CAPTURE = (pathlib.Path(__file__).parents[1] / "fixtures" / "hirobus_tu.bin").read_bytes()


def _no_fetch(url):
    raise AssertionError(f"{url} must not be fetched")


@pytest.fixture
def agency(pg_conn) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES ('広島バス', %s, 'static_join') RETURNING agency_id",
            (FEED_URL,),
        )
        aid = cur.fetchone()[0]
    pg_conn.commit()
    return aid


def _seed_verdict(
    pg_conn,
    aid: int,
    *,
    confirmed: bool,
    expires_at: datetime | None,
    probed_at: datetime = MORNING - timedelta(days=200),
) -> None:
    with pg_conn.cursor() as cur:
        for name in RT_COVERAGE_FIELDS:
            cur.execute(
                "INSERT INTO rt_field_coverage_probes "
                "(agency_id, field_name, confirmed, coverage, sample_size, source_feed, probed_at, expires_at) "
                "VALUES (%s, %s, %s, 1.0, 40, %s, %s, %s)",
                (aid, name, confirmed, FEED_URL, probed_at, expires_at),
            )
    pg_conn.commit()


def _verdict(pg_conn, aid: int) -> dict[str, tuple]:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT field_name, confirmed, sample_size, source_feed, expires_at > now() + interval '170 days', "
            "probed_at > now() - interval '1 hour' "
            "FROM rt_field_coverage_probes WHERE agency_id = %s",
            (aid,),
        )
        rows = {r[0]: r[1:] for r in cur.fetchall()}
    pg_conn.commit()
    return rows


def _runs(pg_conn) -> list[tuple]:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT kind, agency_id, status, rows, error IS NOT NULL, finished_at IS NOT NULL "
            "FROM pipeline_runs ORDER BY run_id"
        )
        rows = cur.fetchall()
    pg_conn.commit()
    return rows


def _run(pg_conn, fetch=lambda _url: CAPTURE, now=MORNING) -> reprobe.ReprobeOutcome:
    return reprobe.reprobe_due_agencies(pg_conn, _database_url(), now=now, fetch=fetch)


def test_an_unprobed_agency_gets_a_complete_live_verdict(pg_conn, agency):
    sample = field_coverage(CAPTURE)["stop_time_updates"]

    outcome = _run(pg_conn)

    assert outcome.recorded == [agency]
    assert _verdict(pg_conn, agency) == {name: (True, sample, FEED_URL, True, True) for name in RT_COVERAGE_FIELDS}
    assert _runs(pg_conn) == [("probe", agency, "ok", sample, False, True)]


def test_an_expired_verdict_is_renewed(pg_conn, agency):
    _seed_verdict(pg_conn, agency, confirmed=True, expires_at=MORNING - timedelta(days=16))

    _run(pg_conn)

    assert all(row[3] and row[4] for row in _verdict(pg_conn, agency).values())


def test_a_live_verdict_is_left_alone_and_draws_nothing(pg_conn, agency):
    _seed_verdict(pg_conn, agency, confirmed=True, expires_at=MORNING + timedelta(days=90))

    outcome = _run(pg_conn, fetch=_no_fetch)

    assert outcome == reprobe.ReprobeOutcome()
    assert _runs(pg_conn) == []


def test_an_overnight_run_fetches_nothing_and_draws_nothing(pg_conn, agency):
    assert _run(pg_conn, fetch=_no_fetch, now=datetime(2026, 10, 27, 0, 5, tzinfo=JST)) == reprobe.ReprobeOutcome()
    assert _runs(pg_conn) == []


@pytest.mark.parametrize(
    ("strategy", "deleted"),
    [("aomori_regex", False), (None, False), ("static_join", True)],
    ids=["a strategy without the fields", "no strategy", "a disabled agency"],
)
def test_an_agency_that_cannot_use_a_verdict_is_never_probed(pg_conn, strategy, deleted):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy, deleted_at) "
            "VALUES ('対象外', %s, %s, CASE WHEN %s THEN now() END)",
            (FEED_URL, strategy, deleted),
        )
    pg_conn.commit()

    assert _run(pg_conn, fetch=_no_fetch) == reprobe.ReprobeOutcome()
    assert _runs(pg_conn) == []


def test_a_thin_poll_writes_no_verdict(pg_conn, agency):
    """Five updates, two with arrivals: 0.4 would pass arr_delay's band here,
    and a quiet minute later 0.5 would refute it. Neither is evidence."""
    thin = header_only_feed(1_793_050_938) + trip_update_feed(
        trip_update(
            "t1",
            *(
                stop_time_update(seq, departure_delay=0, arrival_delay=0 if seq < 3 else None, stop_id=f"s{seq}")
                for seq in range(1, 6)
            ),
        )
    )

    outcome = _run(pg_conn, fetch=lambda _url: thin)

    assert outcome.skipped == [agency]
    assert _verdict(pg_conn, agency) == {}
    assert _runs(pg_conn) == [("probe", agency, "skipped", 5, False, True)]


def test_a_body_that_is_not_a_feed_is_an_error_not_a_quiet_feed(pg_conn, agency):
    """An error page decodes leniently to zero updates, the same as a feed
    with nothing running. It fails the agency instead of being skipped."""
    _seed_verdict(pg_conn, agency, confirmed=True, expires_at=MORNING - timedelta(days=1))
    before = _verdict(pg_conn, agency)

    outcome = _run(pg_conn, fetch=lambda _url: b"<!DOCTYPE html><html><body>502 Bad Gateway</body></html>")

    assert outcome.failed == [agency]
    assert outcome.skipped == []
    assert _verdict(pg_conn, agency) == before
    assert _runs(pg_conn) == [("probe", agency, "error", None, True, True)]


def test_a_refutation_is_refetched_once_a_day_not_on_every_poke(pg_conn, agency):
    _seed_verdict(
        pg_conn,
        agency,
        confirmed=False,
        expires_at=MORNING + timedelta(days=90),
        probed_at=MORNING - timedelta(hours=1),
    )
    assert _run(pg_conn, fetch=_no_fetch) == reprobe.ReprobeOutcome()
    assert _runs(pg_conn) == []

    assert _run(pg_conn, now=MORNING + timedelta(days=1)).recorded == [agency]
    assert all(row[0] for row in _verdict(pg_conn, agency).values())


def _seed_attempt(pg_conn, aid: int, *, status: str, ago: timedelta) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pipeline_runs (kind, agency_id, started_at, finished_at, status) "
            "VALUES ('probe', %s, %s, %s, %s)",
            (aid, MORNING - ago, MORNING - ago + timedelta(seconds=2), status),
        )
    pg_conn.commit()


@pytest.mark.parametrize("status", ["skipped", "error"])
def test_an_attempt_that_wrote_nothing_is_not_retried_inside_the_retry_gap(pg_conn, agency, status):
    """A thin or broken feed leaves the registry as it was, so without the
    gap a deployment poking all day would hit it on every poke."""
    _seed_attempt(pg_conn, agency, status=status, ago=timedelta(hours=1))

    assert _run(pg_conn, fetch=_no_fetch) == reprobe.ReprobeOutcome()
    assert [r[:3] for r in _runs(pg_conn)] == [("probe", agency, status)]


def test_an_attempt_past_the_retry_gap_is_retried(pg_conn, agency):
    _seed_attempt(pg_conn, agency, status="skipped", ago=reprobe.RETRY_AFTER + timedelta(minutes=1))

    assert _run(pg_conn).recorded == [agency]
    assert [r[:3] for r in _runs(pg_conn)] == [("probe", agency, "skipped"), ("probe", agency, "ok")]


def test_a_near_expiry_renewal_whose_first_attempt_was_thin_is_retried_the_same_day(pg_conn, agency):
    """The renewal window exists to give a sometimes-thin feed several tries
    before its verdict lapses; those tries have to land the same day."""
    _seed_verdict(pg_conn, agency, confirmed=True, expires_at=MORNING + timedelta(days=3))
    _seed_attempt(pg_conn, agency, status="skipped", ago=timedelta(hours=1))
    assert _run(pg_conn, fetch=_no_fetch) == reprobe.ReprobeOutcome()

    later = MORNING + reprobe.RETRY_AFTER
    assert later.date() == MORNING.date()
    assert _run(pg_conn, now=later).recorded == [agency]
    assert all(row[3] for row in _verdict(pg_conn, agency).values())


def test_another_agencys_attempt_does_not_hold_this_one_back(pg_conn, agency):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agencies (agency_name, feed_url, ingest_strategy) "
            "VALUES ('広島交通', 'https://feeds.example.jp/realtime/10/trip_updates.bin', 'static_join') "
            "RETURNING agency_id"
        )
        other = cur.fetchone()[0]
    pg_conn.commit()
    _seed_attempt(pg_conn, other, status="error", ago=timedelta(hours=1))

    assert _run(pg_conn).recorded == [agency]


def test_a_failed_fetch_keeps_the_old_verdict_and_records_the_error(pg_conn, agency):
    _seed_verdict(pg_conn, agency, confirmed=False, expires_at=MORNING + timedelta(days=90))
    before = _verdict(pg_conn, agency)

    def _down(_url):
        raise OSError("connection reset by peer")

    outcome = _run(pg_conn, fetch=_down)

    assert outcome.failed == [agency]
    assert _verdict(pg_conn, agency) == before
    assert _runs(pg_conn) == [("probe", agency, "error", None, True, True)]


def test_a_feed_repointed_mid_probe_keeps_its_verdict_off_the_new_feed(pg_conn, agency):
    def _repoint_then_answer(_url):
        other = psycopg2.connect(_database_url())
        try:
            with other.cursor() as cur:
                cur.execute(
                    "UPDATE agencies SET feed_url = %s WHERE agency_id = %s",
                    ("https://feeds.example.jp/realtime/10/trip_updates.bin", agency),
                )
            other.commit()
        finally:
            other.close()
        return CAPTURE

    outcome = _run(pg_conn, fetch=_repoint_then_answer)

    assert outcome.skipped == [agency]
    assert _verdict(pg_conn, agency) == {}


def test_the_cron_sweeps_pass_records_through_the_real_registry(pg_conn, agency, monkeypatch):
    """The sweep's own entry point, on its own connection: a guard that
    swallowed an error here would pass any test that only checks it did
    not raise, so this one checks the verdict landed."""
    from api.routers.internal import _run_rt_coverage_reprobe

    freeze_now(monkeypatch, reprobe, MORNING)
    monkeypatch.setattr(reprobe, "fetch_feed", lambda _url: CAPTURE)

    _run_rt_coverage_reprobe(_database_url())

    assert set(_verdict(pg_conn, agency)) == set(RT_COVERAGE_FIELDS)
    assert [r[:3] for r in _runs(pg_conn)] == [("probe", agency, "ok")]
