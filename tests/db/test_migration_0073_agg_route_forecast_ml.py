"""agg_route_forecast_ml holds one row per upcoming (agency, route,
service_date, hour): the latest published ML forecast for that slot."""

import pytest


@pytest.mark.asyncio
async def test_the_table_accepts_a_forecast_row_and_upserts_on_the_same_slot(aconn):
    await aconn.execute(
        "INSERT INTO agencies (agency_id, agency_name, feed_url) VALUES (8, 'Test Agency', 'https://example.test') "
        "ON CONFLICT DO NOTHING"
    )
    columns = (
        "agency_id, route_code, service_date, hour, mean_min, q10, q50, q90, model_version, origin_date, generated_at"
    )
    await aconn.execute(
        f"INSERT INTO agg_route_forecast_ml ({columns}) "
        "VALUES (8, 'R1', '2026-10-12', 8, 3.0, 1.0, 3.0, 5.0, '2026-10-10', '2026-10-11', now())"
    )
    await aconn.execute(
        f"INSERT INTO agg_route_forecast_ml ({columns}) "
        "VALUES (8, 'R1', '2026-10-12', 8, 4.0, 2.0, 4.0, 6.0, '2026-10-11', '2026-10-12', now()) "
        "ON CONFLICT (agency_id, route_code, service_date, hour) DO UPDATE SET mean_min = EXCLUDED.mean_min, "
        "q10 = EXCLUDED.q10, q50 = EXCLUDED.q50, q90 = EXCLUDED.q90, model_version = EXCLUDED.model_version, "
        "origin_date = EXCLUDED.origin_date, generated_at = EXCLUDED.generated_at"
    )
    row = await aconn.fetchrow(
        "SELECT mean_min, origin_date FROM agg_route_forecast_ml WHERE agency_id = 8 AND route_code = 'R1'"
    )
    assert row["mean_min"] == 4.0
    count = await aconn.fetchval("SELECT count(*) FROM agg_route_forecast_ml WHERE agency_id = 8")
    assert count == 1
