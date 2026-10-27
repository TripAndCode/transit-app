"""The ML forecast overlay on /forecast/heatmap, behind the ml_forecast flag
and a 2-day origin_date freshness check."""

from datetime import date, timedelta

from api.range import jst_today
from pipeline import flags

_COLUMNS = (
    "agency_id, route_code, service_date, hour, mean_min, q10, q50, q90, model_version, origin_date, generated_at"
)


async def _seed_ml_row(aconn, agency_id: int, route_code: str, origin_date: date) -> None:
    await aconn.execute(
        f"INSERT INTO agg_route_forecast_ml ({_COLUMNS}) "
        "VALUES ($1, $2, $3, $4, 9.0, 7.0, 9.0, 11.0, 'v1', $5, now()) "
        "ON CONFLICT (agency_id, route_code, service_date, hour) DO UPDATE SET mean_min = EXCLUDED.mean_min",
        agency_id,
        route_code,
        origin_date + timedelta(days=1),
        8,
        origin_date,
    )


async def _seed_raw_ml_row(
    aconn, agency_id: int, route_code: str, *, service_date: date, hour: int, mean_min: float, origin_date: date
) -> None:
    await aconn.execute(
        f"INSERT INTO agg_route_forecast_ml ({_COLUMNS}) "
        "VALUES ($1, $2, $3, $4, $5, $5, $5, $5, 'v1', $6, now()) "
        "ON CONFLICT (agency_id, route_code, service_date, hour) DO UPDATE SET mean_min = EXCLUDED.mean_min",
        agency_id,
        route_code,
        service_date,
        hour,
        mean_min,
        origin_date,
    )


async def test_heatmap_serves_the_ml_value_when_the_flag_is_on_and_the_row_is_fresh(
    client, aconn, aagency_id, monkeypatch
):
    monkeypatch.setenv("ML_FORECAST_ENABLED", "true")
    flags.reset_cache()
    await _seed_ml_row(aconn, aagency_id, "R1", jst_today())

    response = await client.get(f"/api/{aagency_id}/forecast/heatmap", params={"route": "R1"})

    target_dow = (jst_today() + timedelta(days=1)).isoweekday()
    body = response.json()
    cell = next(c for c in body["cells"] if c["dow"] == target_dow and c["hour"] == 8)
    assert cell["source"] == "ml"
    assert cell["expected_avg_min"] == 9.0


async def test_heatmap_falls_back_to_baseline_when_the_flag_is_off(client, aconn, aagency_id, monkeypatch):
    monkeypatch.setenv("ML_FORECAST_ENABLED", "false")
    flags.reset_cache()
    await _seed_ml_row(aconn, aagency_id, "R1", jst_today())

    response = await client.get(f"/api/{aagency_id}/forecast/heatmap", params={"route": "R1"})

    body = response.json()
    assert all(c["source"] == "baseline" for c in body["cells"])


async def test_heatmap_falls_back_to_baseline_when_the_ml_row_is_stale(client, aconn, aagency_id, monkeypatch):
    monkeypatch.setenv("ML_FORECAST_ENABLED", "true")
    flags.reset_cache()
    await _seed_ml_row(aconn, aagency_id, "R1", jst_today() - timedelta(days=5))

    response = await client.get(f"/api/{aagency_id}/forecast/heatmap", params={"route": "R1"})

    body = response.json()
    assert all(c["source"] == "baseline" for c in body["cells"])


async def test_overview_serves_the_ml_value_when_the_flag_is_on_and_the_row_is_fresh(
    client, aconn, aagency_id, monkeypatch
):
    monkeypatch.setenv("ML_FORECAST_ENABLED", "true")
    flags.reset_cache()
    await _seed_ml_row(aconn, aagency_id, "R1", jst_today())  # mean_min=9.0, hour 8 -> band "morning"

    response = await client.get(f"/api/{aagency_id}/forecast/overview")

    target_dow = (jst_today() + timedelta(days=1)).isoweekday()
    body = response.json()
    cell = next(c for c in body["grid"] if c["dow"] == target_dow and c["band"] == "morning")
    assert cell["source"] == "ml"
    assert cell["expected_avg_min"] == 9.0


async def test_overview_averages_multiple_routes_sharing_a_band_instead_of_dropping_one(
    client, aconn, aagency_id, monkeypatch
):
    # _fetch_ml_cells must not collapse several routes sharing a (dow, hour)
    # slot into one dict entry -- the agency-wide overview (unlike the
    # single-route heatmap) can have more than one.
    monkeypatch.setenv("ML_FORECAST_ENABLED", "true")
    flags.reset_cache()
    target_service_date = jst_today() + timedelta(days=1)
    await _seed_ml_row(aconn, aagency_id, "R1", jst_today())  # mean_min=9.0, hour 8
    await aconn.execute(
        f"INSERT INTO agg_route_forecast_ml ({_COLUMNS}) VALUES ($1, 'R2', $2, 8, 5.0, 3.0, 5.0, 7.0, 'v1', $3, now())",
        aagency_id,
        target_service_date,
        jst_today(),
    )

    response = await client.get(f"/api/{aagency_id}/forecast/overview")

    target_dow = target_service_date.isoweekday()
    body = response.json()
    cell = next(c for c in body["grid"] if c["dow"] == target_dow and c["band"] == "morning")
    assert cell["source"] == "ml"
    assert cell["expected_avg_min"] == 7.0  # (9.0 + 5.0) / 2, not one or the other


async def test_overview_leaves_out_a_run_past_midnight_of_its_service_day(client, aconn, aagency_id, monkeypatch):
    monkeypatch.setenv("ML_FORECAST_ENABLED", "true")
    flags.reset_cache()
    await _seed_ml_row(aconn, aagency_id, "R1", jst_today())  # mean_min=9.0, hour 8
    await _seed_raw_ml_row(
        aconn,
        aagency_id,
        "R1",
        service_date=jst_today() + timedelta(days=1),
        hour=25,
        mean_min=30.0,
        origin_date=jst_today(),
    )

    response = await client.get(f"/api/{aagency_id}/forecast/overview")

    assert response.status_code == 200
    target_dow = (jst_today() + timedelta(days=1)).isoweekday()
    cell = next(c for c in response.json()["grid"] if c["dow"] == target_dow and c["band"] == "morning")
    assert cell["source"] == "ml"
    assert cell["expected_avg_min"] == 9.0


async def test_overview_falls_back_to_baseline_when_the_flag_is_off(client, aconn, aagency_id, monkeypatch):
    monkeypatch.setenv("ML_FORECAST_ENABLED", "false")
    flags.reset_cache()
    await _seed_ml_row(aconn, aagency_id, "R1", jst_today())

    response = await client.get(f"/api/{aagency_id}/forecast/overview")

    body = response.json()
    assert all(c["source"] == "baseline" for c in body["grid"])


async def test_heatmap_does_not_let_a_stale_leftover_row_collide_with_the_new_horizon(
    client, aconn, aagency_id, monkeypatch
):
    # A route's own row for service_date=today (last written by an earlier
    # run's horizon, before today's own horizon moved past it) and a fresh
    # row for service_date=today+7 share the same weekday+hour -- both pass
    # the 2-day origin_date freshness check, but only the upcoming one
    # (today+7) is in the forward horizon this week's forecast actually
    # covers; the stale leftover must not win the (dow, hour) slot.
    monkeypatch.setenv("ML_FORECAST_ENABLED", "true")
    flags.reset_cache()
    # The fresh row is inserted FIRST and the stale leftover SECOND -- an
    # un-ORDER-BY'd "last dict write wins" bug would pick the stale 2.0 here,
    # not the correct 9.0, so this ordering actually discriminates the fix
    # from the bug instead of passing by insertion-order coincidence.
    await _seed_raw_ml_row(
        aconn,
        aagency_id,
        "R1",
        service_date=jst_today() + timedelta(days=7),
        hour=8,
        mean_min=9.0,
        origin_date=jst_today(),
    )
    await _seed_raw_ml_row(
        aconn,
        aagency_id,
        "R1",
        service_date=jst_today(),
        hour=8,
        mean_min=2.0,
        origin_date=jst_today() - timedelta(days=1),
    )

    response = await client.get(f"/api/{aagency_id}/forecast/heatmap", params={"route": "R1"})

    target_dow = jst_today().isoweekday()
    body = response.json()
    cell = next(c for c in body["cells"] if c["dow"] == target_dow and c["hour"] == 8)
    assert cell["source"] == "ml"
    assert cell["expected_avg_min"] == 9.0


async def test_heatmap_disclaimer_acknowledges_the_ml_overlay_when_any_cell_uses_it(
    client, aconn, aagency_id, monkeypatch
):
    monkeypatch.setenv("ML_FORECAST_ENABLED", "true")
    flags.reset_cache()
    await _seed_ml_row(aconn, aagency_id, "R1", jst_today())

    response = await client.get(f"/api/{aagency_id}/forecast/heatmap", params={"route": "R1"})

    body = response.json()
    assert "model" in body["disclaimer"] or "予測モデル" in body["disclaimer"]


async def test_heatmap_flags_an_ml_cell_with_no_baseline_history_as_low_confidence(
    client, aconn, aagency_id, monkeypatch
):
    # No agg_route_hour_dow row exists for this slot at all (zero baseline
    # samples) -- an ML prediction with nothing to compare against must not
    # present as more confident than a 1-sample baseline cell would.
    monkeypatch.setenv("ML_FORECAST_ENABLED", "true")
    flags.reset_cache()
    await _seed_ml_row(aconn, aagency_id, "R1", jst_today())

    response = await client.get(f"/api/{aagency_id}/forecast/heatmap", params={"route": "R1"})

    target_dow = (jst_today() + timedelta(days=1)).isoweekday()
    body = response.json()
    cell = next(c for c in body["cells"] if c["dow"] == target_dow and c["hour"] == 8)
    assert cell["samples"] == 0
    assert cell["low_confidence"] is True


async def test_overview_and_heatmap_name_the_dates_their_ml_rows_forecast(client, aconn, aagency_id, monkeypatch):
    # Service dates one to five days out stay inside the horizon whichever
    # side of the UTC date change the JST clock is on.
    monkeypatch.setenv("ML_FORECAST_ENABLED", "true")
    flags.reset_cache()
    later, sooner = jst_today() + timedelta(days=3), jst_today() + timedelta(days=1)
    for day in (later, sooner):
        await _seed_raw_ml_row(aconn, aagency_id, "R1", service_date=day, hour=8, mean_min=4.0, origin_date=jst_today())
    await _seed_raw_ml_row(aconn, aagency_id, "R2", service_date=sooner, hour=17, mean_min=6.0, origin_date=jst_today())

    overview = (await client.get(f"/api/{aagency_id}/forecast/overview")).json()
    r1 = (await client.get(f"/api/{aagency_id}/forecast/heatmap", params={"route": "R1"})).json()
    r2 = (await client.get(f"/api/{aagency_id}/forecast/heatmap", params={"route": "R2"})).json()

    def named(*days):
        return [{"dow": d.isoweekday(), "service_date": d.isoformat()} for d in days]

    assert overview["forecast_days"] == named(sooner, later)
    assert r1["forecast_days"] == named(sooner, later)
    # A route's own view names only the days its own forecast covers.
    assert r2["forecast_days"] == named(sooner)


async def test_no_date_is_named_as_forecast_while_the_flag_is_off(client, aconn, aagency_id, monkeypatch):
    monkeypatch.setenv("ML_FORECAST_ENABLED", "false")
    flags.reset_cache()
    await _seed_ml_row(aconn, aagency_id, "R1", jst_today())

    overview = (await client.get(f"/api/{aagency_id}/forecast/overview")).json()
    heatmap = (await client.get(f"/api/{aagency_id}/forecast/heatmap", params={"route": "R1"})).json()

    assert overview["forecast_days"] == []
    assert heatmap["forecast_days"] == []
