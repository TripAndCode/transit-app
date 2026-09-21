"""``api.range.ctx_payload`` — the one client-facing projection of a RangeCtx.

Every endpoint that echoes the resolved range back to the caller goes through
this helper, so a single frontend reader can parse the echo from any of them.
"""

from datetime import date

from api.range import RangeCtx, ctx_payload

_CTX_KEYS = {"from", "to", "dow", "time_band", "service", "routes"}


def test_ctx_payload_uses_the_client_facing_from_and_to_keys():
    payload = ctx_payload(RangeCtx(from_date=date(2026, 8, 1), to_date=date(2026, 8, 31)))
    assert payload == {
        "from": "2026-08-01",
        "to": "2026-08-31",
        "dow": "all",
        "time_band": "all",
        "service": "all",
        "routes": [],
    }


def test_ctx_payload_echoes_every_filter_dimension():
    ctx = RangeCtx(
        from_date=date(2026, 1, 2),
        to_date=date(2026, 1, 9),
        dow="weekday",
        time_band="morning",
        service="平日",
        routes=("1021", "5"),
    )
    assert ctx_payload(ctx) == {
        "from": "2026-01-02",
        "to": "2026-01-09",
        "dow": "weekday",
        "time_band": "morning",
        "service": "平日",
        "routes": ["1021", "5"],
    }


def test_ctx_payload_routes_is_a_detached_list():
    """The payload is handed to Pydantic/JSON encoders that may mutate it;
    RangeCtx is frozen and must not share the tuple's contents with it."""
    ctx = RangeCtx(from_date=date(2026, 1, 1), to_date=date(2026, 1, 2), routes=("A",))
    payload = ctx_payload(ctx)
    payload["routes"].append("B")
    assert ctx.routes == ("A",)


def test_report_ctx_model_is_built_from_the_shared_payload():
    """``/reports/*`` keeps a typed ctx, but its field set and serialized keys
    must stay identical to every other endpoint's echo."""
    from api.routers.reports import ReportCtx, _report_ctx

    ctx = RangeCtx(
        from_date=date(2026, 3, 1),
        to_date=date(2026, 3, 7),
        dow="weekend",
        time_band="evening",
        service="土日祝",
        routes=("7",),
    )
    model = _report_ctx(ctx)
    assert isinstance(model, ReportCtx)
    assert model.model_dump(by_alias=True) == ctx_payload(ctx)
    assert set(model.model_dump(by_alias=True)) == _CTX_KEYS


def test_no_router_hand_rolls_the_ctx_echo():
    """A second inline copy of the echo dict is exactly how the keys drift
    apart between endpoints, so the literal must appear nowhere but the helper."""
    from pathlib import Path

    routers = Path(__file__).resolve().parents[2] / "api" / "routers"
    offenders = sorted(p.name for p in routers.glob("*.py") if '"from": ctx.from_date.isoformat()' in p.read_text())
    assert offenders == [], f"These still build the ctx echo by hand: {offenders}"
