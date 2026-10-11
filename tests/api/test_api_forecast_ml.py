"""The ML forecast overlay on /forecast/heatmap, behind the ml_forecast flag
and a 2-day origin_date freshness check."""

from datetime import date, timedelta

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


async def test_heatmap_serves_the_ml_value_when_the_flag_is_on_and_the_row_is_fresh(
    client, aconn, aagency_id, monkeypatch
):
    monkeypatch.setenv("ML_FORECAST_ENABLED", "true")
    flags.reset_cache()
    await _seed_ml_row(aconn, aagency_id, "R1", date.today())

    response = await client.get(f"/api/{aagency_id}/forecast/heatmap", params={"route": "R1"})

    target_dow = (date.today() + timedelta(days=1)).isoweekday()
    body = response.json()
    cell = next(c for c in body["cells"] if c["dow"] == target_dow and c["hour"] == 8)
    assert cell["source"] == "ml"
    assert cell["expected_avg_min"] == 9.0


async def test_heatmap_falls_back_to_baseline_when_the_flag_is_off(client, aconn, aagency_id, monkeypatch):
    monkeypatch.setenv("ML_FORECAST_ENABLED", "false")
    flags.reset_cache()
    await _seed_ml_row(aconn, aagency_id, "R1", date.today())

    response = await client.get(f"/api/{aagency_id}/forecast/heatmap", params={"route": "R1"})

    body = response.json()
    assert all(c["source"] == "baseline" for c in body["cells"])


async def test_heatmap_falls_back_to_baseline_when_the_ml_row_is_stale(client, aconn, aagency_id, monkeypatch):
    monkeypatch.setenv("ML_FORECAST_ENABLED", "true")
    flags.reset_cache()
    await _seed_ml_row(aconn, aagency_id, "R1", date.today() - timedelta(days=5))

    response = await client.get(f"/api/{aagency_id}/forecast/heatmap", params={"route": "R1"})

    body = response.json()
    assert all(c["source"] == "baseline" for c in body["cells"])


async def test_overview_serves_the_ml_value_when_the_flag_is_on_and_the_row_is_fresh(
    client, aconn, aagency_id, monkeypatch
):
    monkeypatch.setenv("ML_FORECAST_ENABLED", "true")
    flags.reset_cache()
    await _seed_ml_row(aconn, aagency_id, "R1", date.today())  # mean_min=9.0, hour 8 -> band "morning"

    response = await client.get(f"/api/{aagency_id}/forecast/overview")

    target_dow = (date.today() + timedelta(days=1)).isoweekday()
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
    target_service_date = date.today() + timedelta(days=1)
    await _seed_ml_row(aconn, aagency_id, "R1", date.today())  # mean_min=9.0, hour 8
    await aconn.execute(
        f"INSERT INTO agg_route_forecast_ml ({_COLUMNS}) VALUES ($1, 'R2', $2, 8, 5.0, 3.0, 5.0, 7.0, 'v1', $3, now())",
        aagency_id,
        target_service_date,
        date.today(),
    )

    response = await client.get(f"/api/{aagency_id}/forecast/overview")

    target_dow = target_service_date.isoweekday()
    body = response.json()
    cell = next(c for c in body["grid"] if c["dow"] == target_dow and c["band"] == "morning")
    assert cell["source"] == "ml"
    assert cell["expected_avg_min"] == 7.0  # (9.0 + 5.0) / 2, not one or the other


async def test_overview_falls_back_to_baseline_when_the_flag_is_off(client, aconn, aagency_id, monkeypatch):
    monkeypatch.setenv("ML_FORECAST_ENABLED", "false")
    flags.reset_cache()
    await _seed_ml_row(aconn, aagency_id, "R1", date.today())

    response = await client.get(f"/api/{aagency_id}/forecast/overview")

    body = response.json()
    assert all(c["source"] == "baseline" for c in body["grid"])
