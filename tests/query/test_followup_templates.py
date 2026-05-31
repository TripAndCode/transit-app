"""Per-tool follow-up template tests."""

from datetime import date

from pipeline.query.followup_templates import FOLLOWUPS, generate_followups
from pipeline.query.intent import canonicalize, signature_hash


def _ctx():
    return {"from_date": date(2026, 5, 1), "to_date": date(2026, 5, 30)}


def test_every_tool_has_at_least_two_followups():
    for tool in ("top_n", "time_series", "compare_segments", "route_stats", "describe_data"):
        assert tool in FOLLOWUPS, f"missing followups for {tool}"
        assert len(FOLLOWUPS[tool]) >= 2


def test_followups_change_the_hash():
    """A follow-up should produce different canonical args than the input."""
    base_args = {"metric": "avg_delay", "n": 10}
    base_hash = signature_hash("top_n", canonicalize("top_n", base_args, _ctx()))
    fs = generate_followups("top_n", base_args, _ctx(), result_first_row=None)
    assert fs, "top_n must produce at least one follow-up"
    for f in fs:
        new_hash = signature_hash(f["tool"], canonicalize(f["tool"], f["args"], _ctx()))
        assert new_hash != base_hash or f["tool"] != "top_n", (
            f"follow-up {f['id']} produced identical hash + tool to the input"
        )


def test_describe_data_pagination_followup():
    """describe_data with offset=0 should suggest offset=50 next."""
    fs = generate_followups("describe_data", {"kind": "routes"}, _ctx(), result_first_row=None)
    next_page = next((f for f in fs if "offset" in f["args"]), None)
    assert next_page is not None
    assert next_page["args"]["offset"] == 50


def test_followups_capped_at_five():
    fs = generate_followups(
        "top_n",
        {"metric": "avg_delay", "n": 10},
        _ctx(),
        result_first_row=["16071", "weekday", 8.27],
    )
    assert len(fs) <= 5
