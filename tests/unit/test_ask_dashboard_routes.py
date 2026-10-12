"""The dashboard endpoints read `routes` like every other scope endpoint."""

from api.routers.ask_dashboard import _resolve_ctx


def _routes(*raw: str) -> tuple[str, ...]:
    return tuple(_resolve_ctx(None, None, "all", "all", "all", raw).routes)


def test_a_comma_separated_routes_value_is_several_routes():
    assert set(_routes("R1,R2")) == {"R1", "R2"}


def test_repeated_routes_keys_still_work():
    assert set(_routes("R1", "R2")) == {"R1", "R2"}


def test_a_comma_list_and_a_repeated_key_mix_and_drop_blanks():
    assert set(_routes("R1,,R2", "R3,")) == {"R1", "R2", "R3"}
