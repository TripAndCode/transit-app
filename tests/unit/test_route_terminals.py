from collections import Counter

import pytest

from pipeline.route_terminals import RouteTerminals, derive_route_terminals, normalize_stop_name


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("青森駅 ③のりば", "青森駅"),
        ("青森駅 (降車専用)", "青森駅"),
        ("県立中央病院前 （病院構内）", "県立中央病院前"),
        ("みなと小学校下（旧長江中学校前)", "みなと小学校下"),
        ("尾道駅前⑦番", "尾道駅前"),
        ("新尾道駅①番のりば", "新尾道駅"),
        ("古川 ⑫乗り場", "古川"),
        ("  横川駅　 前 ", "横川駅 前"),
        ("あさひが丘３番", "あさひが丘３番"),
        ("東のりば", "東のりば"),
        ("(降車専用)", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_stop_name_drops_platform_and_note_annotations(raw, normalized):
    assert normalize_stop_name(raw) == normalized


def test_routes_jp_names_the_route_without_any_trips():
    assert derive_route_terminals(("広島バスセンター", "", "あさひが丘下"), Counter()) == RouteTerminals(
        "広島バスセンター", None, "あさひが丘下", False, False
    )


def test_routes_jp_names_that_differ_only_by_platform_make_a_loop():
    assert derive_route_terminals(("青森駅 ③のりば", None, "青森駅 (降車専用)"), Counter()) == RouteTerminals(
        "青森駅", None, "青森駅", True, False
    )


def test_without_routes_jp_the_dominant_trip_pair_names_the_route():
    pairs = Counter({("A", "B"): 5, ("A", "C"): 2, ("B", "A"): 1})
    assert derive_route_terminals(None, pairs) == RouteTerminals("A", None, "B", False, False)


def test_routes_jp_fills_what_it_names_and_the_trips_fill_the_rest():
    pairs = Counter({("A", "X"): 5})
    assert derive_route_terminals((None, "経由", " X "), pairs) == RouteTerminals("A", "経由", "X", False, False)


@pytest.mark.parametrize(
    ("routes_jp", "pairs", "expected"),
    [
        (("A", None, None), Counter({("A", "B"): 3}), ("A", None, "B")),
        (("A", None, None), Counter({("B", "A"): 3}), ("A", None, "B")),
        ((None, None, "A"), Counter({("A", "B"): 3}), ("B", None, "A")),
        ((None, None, "A"), Counter({("B", "A"): 3}), ("B", None, "A")),
        (("A", None, None), Counter({("B", "C"): 5, ("C", "A"): 2}), ("A", None, "C")),
        (("A", None, None), Counter({("B", "C"): 5}), ("A", None, None)),
    ],
)
def test_one_routes_jp_end_takes_the_other_from_the_top_pair_that_contains_it(routes_jp, pairs, expected):
    terminals = derive_route_terminals(routes_jp, pairs)
    assert terminals[:3] == expected
    assert terminals.is_loop is False


def test_a_tie_between_pairs_goes_to_the_pair_that_sorts_first():
    pairs = Counter({("B", "A"): 2, ("A", "B"): 2})
    assert derive_route_terminals(None, pairs) == RouteTerminals("A", None, "B", False, True)


@pytest.mark.parametrize(
    ("forward", "reverse", "bidirectional"),
    [(10, 3, True), (4, 1, False), (5, 5, True), (5, 0, False)],
)
def test_bidirectional_needs_the_way_back_on_at_least_30_percent_as_many_trips(forward, reverse, bidirectional):
    pairs = Counter({("A", "B"): forward, ("B", "A"): reverse, ("A", "C"): forward - 1})
    assert derive_route_terminals(None, pairs).bidirectional is bidirectional


def test_bidirectional_comes_from_the_trips_even_when_routes_jp_names_the_route():
    pairs = Counter({("A", "B"): 5, ("B", "A"): 4})
    assert derive_route_terminals(("X", None, "Y"), pairs) == RouteTerminals("X", None, "Y", False, True)


def test_a_trip_loop_is_a_loop_and_never_bidirectional():
    assert derive_route_terminals(None, Counter({("A", "A"): 4})) == RouteTerminals("A", None, "A", True, False)


def test_a_route_routes_jp_makes_a_loop_is_not_also_bidirectional():
    pairs = Counter({("A", "B"): 5, ("B", "A"): 5})
    assert derive_route_terminals(("A", None, "A"), pairs) == RouteTerminals("A", None, "A", True, False)


def test_a_route_with_no_names_anywhere_has_none():
    assert derive_route_terminals(None, Counter()) == RouteTerminals(None, None, None, False, False)
