"""Pure assembly + SQL-text coverage for the admin agency diagnostics payload.

No DB: every SQL statement is asserted as rendered text against the column
names the migrations actually define, and every shaping rule is a pure
function over already-fetched rows.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from api import agency_diagnostics as ad

UTC = timezone.utc


# ── SQL text ─────────────────────────────────────────────────────────────


def test_rt_coverage_sql_reads_the_probe_registry_columns():
    sql = ad.RT_COVERAGE_SQL
    assert "FROM rt_field_coverage_probes" in sql
    for col in ("field_name", "confirmed", "coverage", "sample_size", "source_feed", "probed_at", "expires_at"):
        assert col in sql
    assert "WHERE agency_id = $1" in sql


def test_clamp_history_sql_reads_agg_feed_health():
    sql = ad.CLAMP_HISTORY_SQL
    assert "FROM agg_feed_health" in sql
    assert "raw_samples" in sql and "clamp_count" in sql
    assert "ORDER BY date" in sql


def test_static_versions_sql_reads_the_version_summary_and_current_trips():
    sql = ad.STATIC_VERSIONS_SQL
    assert "FROM agg_static_version_summary" in sql
    assert "static_version_id" in sql
    assert "trip_count" in sql
    assert "vehicle_km" in sql
    assert "computed_at" in sql
    # routes/calendar are not in the summary table; they come from the
    # currently-loaded static rows.
    assert "static_trips" in sql
    assert "static_calendar_dates" in sql


def test_weather_station_sql_reads_the_mapping_table():
    sql = ad.WEATHER_STATION_SQL
    assert "FROM agency_weather_stations" in sql
    assert "station_id" in sql and "station_name" in sql


def test_standards_sql_matches_the_standards_table():
    sql = ad.STANDARDS_SQL
    assert "FROM route_performance_standards" in sql
    for col in ("route_code", "metric_type", "threshold_value", "bonus_malus_rate"):
        assert col in sql


def test_weights_sql_matches_the_weights_table():
    sql = ad.WEIGHTS_SQL
    assert "FROM ridership_weights" in sql
    assert "route_code" in sql and "weight" in sql


def test_weight_upserts_target_the_two_partial_unique_indexes():
    # ridership_weights has no table-level unique constraint: the default row
    # and the per-route rows are each covered by their own partial index, so
    # ON CONFLICT has to name the matching predicate or the upsert errors.
    assert "ON CONFLICT (agency_id, route_code) WHERE route_code IS NOT NULL" in ad.UPSERT_ROUTE_WEIGHT_SQL
    assert "ON CONFLICT (agency_id) WHERE route_code IS NULL" in ad.UPSERT_DEFAULT_WEIGHT_SQL


def test_standard_upsert_targets_the_agency_route_metric_index():
    assert "ON CONFLICT (agency_id, route_code, metric_type)" in ad.UPSERT_STANDARD_SQL


# ── freshness ────────────────────────────────────────────────────────────


def test_freshness_is_unknown_without_any_aggregated_day():
    assert ad.freshness_state(None, date(2026, 9, 20)) == "unknown"


def test_freshness_is_fresh_through_yesterday():
    today = date(2026, 9, 20)
    assert ad.freshness_state(date(2026, 9, 19), today) == "fresh"
    assert ad.freshness_state(date(2026, 9, 20), today) == "fresh"


def test_freshness_is_stale_once_the_newest_day_falls_behind_yesterday():
    assert ad.freshness_state(date(2026, 9, 18), date(2026, 9, 20)) == "stale"


# ── RT field coverage ────────────────────────────────────────────────────


def _probe(field, confirmed=True, coverage=0.99, sample=1200, expires_in_days=30):
    now = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
    return {
        "field_name": field,
        "confirmed": confirmed,
        "coverage": coverage,
        "sample_size": sample,
        "source_feed": "https://feed.example.jp/tu.bin",
        "probed_at": now - timedelta(days=1),
        "expires_at": None if expires_in_days is None else now + timedelta(days=expires_in_days),
    }


def test_rt_coverage_reports_every_registry_field_even_when_never_probed():
    cov = ad.build_rt_coverage([], now=datetime(2026, 9, 20, 3, 0, tzinfo=UTC))
    assert set(cov["fields"]) == set(ad.RT_COVERAGE_FIELDS)
    for field in ad.RT_COVERAGE_FIELDS:
        assert cov["fields"][field] == {
            "present": False,
            "coverage_pct": None,
            "sample_size": None,
            "probed_at": None,
            "expired": False,
            "probed": False,
        }
    assert cov["complete"] is False
    assert cov["last_probed_at"] is None


def test_rt_coverage_marks_a_confirmed_live_probe_present():
    now = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
    cov = ad.build_rt_coverage([_probe("stop_id", coverage=0.985)], now=now)
    f = cov["fields"]["stop_id"]
    assert f["present"] is True
    assert f["probed"] is True
    assert f["coverage_pct"] == 98.5
    assert f["sample_size"] == 1200
    assert f["expired"] is False
    assert cov["last_probed_at"] == (now - timedelta(days=1)).isoformat()


def test_rt_coverage_treats_an_expired_verdict_as_not_present():
    now = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
    stale = _probe("arr_delay", expires_in_days=-1)
    cov = ad.build_rt_coverage([stale], now=now)
    assert cov["fields"]["arr_delay"]["expired"] is True
    assert cov["fields"]["arr_delay"]["present"] is False
    assert cov["fields"]["arr_delay"]["probed"] is True


def test_rt_coverage_is_complete_only_when_every_field_is_live_and_confirmed():
    now = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
    rows = [_probe(f) for f in ad.RT_COVERAGE_FIELDS]
    assert ad.build_rt_coverage(rows, now=now)["complete"] is True
    rows[-1]["confirmed"] = False
    assert ad.build_rt_coverage(rows, now=now)["complete"] is False


def test_rt_coverage_never_expires_a_row_with_no_expiry():
    now = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
    cov = ad.build_rt_coverage([_probe("stop_id", expires_in_days=None)], now=now)
    assert cov["fields"]["stop_id"]["expired"] is False
    assert cov["fields"]["stop_id"]["present"] is True


# ── clamp history ────────────────────────────────────────────────────────


def test_clamp_history_covers_the_whole_window_with_gaps_as_none():
    today = date(2026, 9, 20)
    rows = [{"date": date(2026, 9, 19), "raw_samples": 1000, "clamp_count": 15}]
    hist = ad.build_clamp_history(rows, today=today, days=14)
    assert len(hist) == 14
    assert hist[0]["date"] == "2026-09-07"
    assert hist[-1]["date"] == "2026-09-20"
    by_date = {h["date"]: h["clamp_pct"] for h in hist}
    assert by_date["2026-09-19"] == 1.5
    assert by_date["2026-09-20"] is None


def test_clamp_history_reports_none_rather_than_zero_for_a_day_with_no_samples():
    hist = ad.build_clamp_history(
        [{"date": date(2026, 9, 20), "raw_samples": 0, "clamp_count": 0}],
        today=date(2026, 9, 20),
        days=1,
    )
    assert hist == [{"date": "2026-09-20", "clamp_pct": None}]


# ── static versions ──────────────────────────────────────────────────────


def test_static_versions_shapes_the_timeline_newest_first():
    rows = [
        {
            "version": "v2026-09-01",
            "loaded_at": datetime(2026, 9, 1, 2, 0, tzinfo=UTC),
            "trips": 1204,
            "vehicle_km": 18321.5,
            "routes": 12,
            "calendar_until": "20270331",
            "is_current": True,
        },
        {
            "version": "v2026-04-01",
            "loaded_at": datetime(2026, 4, 1, 2, 0, tzinfo=UTC),
            "trips": 1188,
            "vehicle_km": None,
            "routes": None,
            "calendar_until": None,
            "is_current": False,
        },
    ]
    out = ad.build_static_versions(rows)
    assert [v["version"] for v in out] == ["v2026-09-01", "v2026-04-01"]
    assert out[0]["loaded_at"] == "2026-09-01T02:00:00+00:00"
    assert out[0]["calendar_until"] == "2027-03-31"
    assert out[0]["is_current"] is True
    assert out[1]["routes"] is None
    assert out[1]["calendar_until"] is None


def test_static_versions_passes_through_an_already_dashed_calendar_date():
    rows = [
        {
            "version": "v1",
            "loaded_at": None,
            "trips": 1,
            "vehicle_km": None,
            "routes": None,
            "calendar_until": "2027-03-31",
            "is_current": False,
        }
    ]
    assert ad.build_static_versions(rows)[0]["calendar_until"] == "2027-03-31"


# ── weights coverage ─────────────────────────────────────────────────────


def test_weights_coverage_counts_routes():
    assert ad.build_weights_coverage(3, 12) == {"routes_with_weights": 3, "routes_total": 12}


def test_weights_coverage_treats_missing_counts_as_zero():
    assert ad.build_weights_coverage(None, None) == {"routes_with_weights": 0, "routes_total": 0}


# ── edit validation ──────────────────────────────────────────────────────


def test_standard_edits_reject_an_unknown_metric_type():
    with pytest.raises(ValueError, match="metric_type"):
        ad.validate_standard_edits(
            [{"route_code": "42", "metric_type": "vibes", "threshold_value": 1.0, "bonus_malus_rate": 0.0}]
        )


def test_standard_edits_reject_a_negative_bonus_malus_rate():
    with pytest.raises(ValueError, match="bonus_malus_rate"):
        ad.validate_standard_edits(
            [{"route_code": "42", "metric_type": "ewt_sec", "threshold_value": 120.0, "bonus_malus_rate": -0.1}]
        )


def test_standard_edits_reject_a_blank_route_code():
    with pytest.raises(ValueError, match="route_code"):
        ad.validate_standard_edits(
            [{"route_code": "  ", "metric_type": "ewt_sec", "threshold_value": 120.0, "bonus_malus_rate": 0.0}]
        )


def test_standard_edits_reject_two_rows_for_the_same_route_and_metric():
    item = {"route_code": "42", "metric_type": "ewt_sec", "threshold_value": 120.0, "bonus_malus_rate": 0.0}
    with pytest.raises(ValueError, match="duplicate"):
        ad.validate_standard_edits([item, {**item, "threshold_value": 90.0}])


def test_standard_edits_accept_both_metrics_for_one_route():
    ad.validate_standard_edits(
        [
            {"route_code": "42", "metric_type": "ewt_sec", "threshold_value": 120.0, "bonus_malus_rate": 1.0},
            {
                "route_code": "42",
                "metric_type": "vehicle_km_delivered_pct",
                "threshold_value": 95.0,
                "bonus_malus_rate": 2.0,
            },
        ]
    )


def test_weight_edits_reject_a_non_positive_weight():
    with pytest.raises(ValueError, match="weight"):
        ad.validate_weight_edits([{"route_code": "42", "weight": 0}])


def test_weight_edits_reject_two_rows_for_the_same_route():
    with pytest.raises(ValueError, match="duplicate"):
        ad.validate_weight_edits([{"route_code": "42", "weight": 1}, {"route_code": "42", "weight": 2}])


def test_weight_edits_reject_two_default_rows():
    with pytest.raises(ValueError, match="duplicate"):
        ad.validate_weight_edits([{"route_code": None, "weight": 1}, {"route_code": None, "weight": 2}])


def test_weight_edits_accept_a_default_plus_route_rows():
    ad.validate_weight_edits([{"route_code": None, "weight": 1}, {"route_code": "42", "weight": 3}])


# ── fleet health rows (the list columns) ─────────────────────────────────


def test_agency_health_sql_groups_the_newest_aggregated_day_per_agency():
    sql = ad.AGENCIES_HEALTH_SQL
    assert "FROM agencies a" in sql
    assert "agg_meta" in sql
    assert "GROUP BY agency_id" in sql
    # A correlated MAX() per agency would re-scan agg_route_daily once per
    # row; the list view has to stay one grouped pass.
    assert "MAX(date)" in sql


def test_current_static_version_sql_takes_one_row_per_agency():
    sql = ad.CURRENT_STATIC_VERSION_SQL
    assert "DISTINCT ON (agency_id)" in sql
    assert "FROM agg_static_version_summary" in sql
    assert "ORDER BY agency_id, computed_at DESC" in sql


def _header(agency_id=1, name="Hokuriku", latest=date(2026, 9, 19)):
    return {
        "agency_id": agency_id,
        "agency_name": name,
        "feed_url": "https://feed.example.jp/tu.bin",
        "ingest_strategy": "static_join",
        "deleted_at": None,
        "analyzed_at": datetime(2026, 9, 20, 1, 0, tzinfo=UTC),
        "max_updates_captured_at": datetime(2026, 9, 20, 2, 30, tzinfo=UTC),
        "latest_data_date": latest,
    }


def test_agency_health_joins_every_source_onto_each_agency():
    now = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
    rows = ad.build_agency_health(
        headers=[_header()],
        probes=[dict(_probe(f), agency_id=1) for f in ad.RT_COVERAGE_FIELDS],
        clamps=[{"agency_id": 1, "date": date(2026, 9, 19), "raw_samples": 1000, "clamp_count": 5}],
        versions=[{"agency_id": 1, "version": "v2026-09-01", "loaded_at": datetime(2026, 9, 1, tzinfo=UTC)}],
        today=date(2026, 9, 20),
        now=now,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["agency_id"] == 1
    assert row["freshness"] == "fresh"
    assert row["last_capture_at"] == "2026-09-20T02:30:00+00:00"
    assert row["rt_coverage"]["complete"] is True
    assert row["rt_coverage"]["present_count"] == len(ad.RT_COVERAGE_FIELDS)
    assert row["rt_coverage"]["field_count"] == len(ad.RT_COVERAGE_FIELDS)
    assert len(row["clamp_history"]) == ad.CLAMP_HISTORY_DAYS
    assert row["clamp_history"][-2]["clamp_pct"] == 0.5
    assert row["static_version"] == {"version": "v2026-09-01", "loaded_at": "2026-09-01T00:00:00+00:00"}


def test_agency_health_keeps_an_agency_with_no_diagnostics_at_all():
    rows = ad.build_agency_health(
        headers=[_header(agency_id=7, name="New", latest=None)],
        probes=[],
        clamps=[],
        versions=[],
        today=date(2026, 9, 20),
        now=datetime(2026, 9, 20, 3, 0, tzinfo=UTC),
    )
    row = rows[0]
    assert row["freshness"] == "unknown"
    assert row["rt_coverage"]["present_count"] == 0
    assert row["rt_coverage"]["probed"] is False
    assert row["static_version"] is None
    assert all(d["clamp_pct"] is None for d in row["clamp_history"])


def test_agency_health_never_mixes_two_agencies_rows():
    now = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
    rows = ad.build_agency_health(
        headers=[_header(agency_id=1), _header(agency_id=2, name="Kaga")],
        probes=[dict(_probe("stop_id"), agency_id=2)],
        clamps=[{"agency_id": 2, "date": date(2026, 9, 19), "raw_samples": 100, "clamp_count": 1}],
        versions=[{"agency_id": 2, "version": "v2", "loaded_at": None}],
        today=date(2026, 9, 20),
        now=now,
    )
    first, second = rows
    assert first["rt_coverage"]["present_count"] == 0
    assert first["static_version"] is None
    assert all(d["clamp_pct"] is None for d in first["clamp_history"])
    assert second["rt_coverage"]["present_count"] == 1
    assert second["static_version"]["version"] == "v2"


def test_standard_edits_reject_a_non_finite_threshold():
    # DOUBLE PRECISION stores NaN/Infinity happily, and every figure derived
    # from the threshold afterwards would be NaN.
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError, match="threshold_value"):
            ad.validate_standard_edits(
                [{"route_code": "42", "metric_type": "ewt_sec", "threshold_value": bad, "bonus_malus_rate": 0.0}]
            )
