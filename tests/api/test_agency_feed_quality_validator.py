"""The drawer's validator figures, read through asyncpg as the endpoint reads
them: the rule map arrives as an object, and a month without a validated day
states its reason, never a rule map of zero findings."""

import json
from datetime import date, datetime, timezone

from api import agency_diagnostics as ad
from api.routers.admin_agencies import FeedQualityMonth

_RULES = {"E022": {"severity": "ERROR", "title": "t", "occurrences": 120, "files_with_rule": 58, "days": 1}}


async def _month(aconn, agency_id: int, month: date, validator: dict) -> None:
    names = "".join(f", {name}" for name in validator)
    params = "".join(f", ${i + 4}" for i in range(len(validator)))
    await aconn.execute(
        "INSERT INTO feed_quality_monthly (agency_id, month, through_date, covered_days, window_days, "
        "stop_plan_days, message_age_polls, message_age_undated_polls, raw_samples, clamp_count, stop_time_updates, "
        "rt_completeness_missing, stop_completeness_missing, message_age_missing, clamp_missing, "
        f"field_coverage_missing, vehicle_positions_missing{names}) VALUES ($1, $2, $3, 28, 28, 0, 0, 0, 0, 0, 0, "
        f"'no_plan', 'no_plan', 'no_data', 'no_data', 'no_data', 'not_recorded'{params})",
        agency_id,
        month,
        month.replace(day=28),
        *validator.values(),
    )


async def test_the_drawer_reads_the_validator_figures_as_json_values(aconn):
    agency_id = await aconn.fetchval(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ('a', 'http://a') RETURNING agency_id"
    )
    await _month(
        aconn,
        agency_id,
        date(2026, 10, 1),
        {
            "validator_ok_days": 1,
            "validator_failed_days": 2,
            "validator_files_validated": 58,
            "validator_rules": json.dumps(_RULES),
            "validator_missing": None,
            "validator_ingested_at": datetime(2026, 11, 1, 1, 0, tzinfo=timezone.utc),
        },
    )
    await _month(
        aconn,
        agency_id,
        date(2026, 9, 1),
        {"validator_ok_days": 0, "validator_failed_days": 0, "validator_missing": "not_validated"},
    )
    rows = [dict(r) for r in await aconn.fetch(ad.FEED_QUALITY_SQL, agency_id, ad.FEED_QUALITY_MONTHS)]
    october, september = (FeedQualityMonth(**m).model_dump() for m in ad.build_feed_quality(rows))

    assert october["validator_rules"] == _RULES
    assert (october["validator_ok_days"], october["validator_failed_days"]) == (1, 2)
    assert october["validator_files_validated"] == 58
    assert datetime.fromisoformat(october["validator_ingested_at"]) == datetime(2026, 11, 1, 1, 0, tzinfo=timezone.utc)
    assert (september["validator_rules"], september["validator_files_validated"]) == (None, None)
    assert september["validator_missing"] == "not_validated"
    assert october["grade"] == september["grade"]
