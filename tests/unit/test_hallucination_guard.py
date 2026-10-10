import decimal

from pipeline.query.hallucination_guard import verify_numeric_claims

GROUNDING = {"route": "12", "avg_delay_min": 14.2, "delayed_count": 6, "delta_pct": 56.1}


def test_answer_with_only_grounded_numbers_passes():
    answer = "Route 12 is averaging 14.2 minutes late, up 56.1% (6 routes delayed)."
    assert verify_numeric_claims(answer, GROUNDING) is True


def test_answer_with_fabricated_number_fails():
    answer = "Route 12 is averaging 22.9 minutes late."
    assert verify_numeric_claims(answer, GROUNDING) is False


def test_answer_with_no_numbers_passes_trivially():
    assert verify_numeric_claims("Delays look typical right now.", GROUNDING) is True


def test_rounded_number_still_passes():
    answer = "Route 12 is averaging about 14 minutes late."
    assert verify_numeric_claims(answer, GROUNDING) is True


def test_date_in_grounding_does_not_leak_spurious_negative_numbers():
    grounding = {"from_date": "2026-09-05", "to_date": "2026-09-06", "avg_delay_min": 14.2}
    answer = "Delays dropped by -9% today."
    assert verify_numeric_claims(answer, grounding) is False


def test_hyphen_glued_route_number_still_passes():
    grounding = {"route": "14", "avg_delay_min": 14.2}
    answer = "route-14 is averaging 14.2 minutes late."
    assert verify_numeric_claims(answer, grounding) is True


def test_comma_thousands_separator_still_matches():
    grounding = {"count": 1234}
    answer = "1,234 riders were affected."
    assert verify_numeric_claims(answer, grounding) is True


def test_number_glued_to_preceding_letter_still_extracted():
    grounding = {"platform": 14}
    answer = "Track A14 is affected."
    assert verify_numeric_claims(answer, grounding) is True


def test_decimal_grounding_value_matches():
    grounding = {"avg_min": decimal.Decimal("14.20")}
    answer = "Averaging 14.2 minutes."
    assert verify_numeric_claims(answer, grounding) is True


# With no grounding there is nothing to verify against, so the guard has no
# verdict and the answer passes through. The grounded path below is unchanged.


def test_ungrounded_answer_passes_through_whatever_it_contains():
    """Route codes, periods, counts, clock times, ordinals, list markers — and
    an invented statistic too. None of them can be traced to anything, so this
    function reports no violation rather than guessing which is which."""
    for text in (
        "このチャットでは天気との比較は扱えません。代わりに『22171の平日と土日祝の比較』が答えられます",
        "直近2週間の傾向なら答えられます。",
        "答えられる質問を3件挙げます。",
        "時刻 7時30分の便が最も遅れています",
        "第1位は22171です",
        "1. 22171の遅延\n2. 直近2週間の傾向",
        "上位10路線の遅延ランキングも聞けます",
        "Buses run about every 999 minutes off-peak.",
    ):
        assert verify_numeric_claims(text, {}) is True, text


def test_grounded_path_still_checks_every_number():
    """The narrowing is only about an absent grounding dict. Where grounding
    exists, an unexplained number is still a fabrication."""
    assert verify_numeric_claims("Route 99999 is the worst.", {"route_code": "22171"}) is False
    assert verify_numeric_claims("Route 22171 is the worst.", {"route_code": "22171"}) is True


def test_numbered_list_markers_are_not_claims():
    answer = "1. Route 12 averages 14.2 min.\n2) It is up 56.1%.\n3．6 routes are delayed."
    assert verify_numeric_claims(answer, GROUNDING) is True


def test_a_marker_shape_that_numbers_no_list_is_still_a_claim():
    # A lone marker, or markers not counting 1, 2, 3 ..., order nothing.
    assert verify_numeric_claims("99) Route 12 is badly delayed.", GROUNDING) is False
    assert verify_numeric_claims("1. Route 12 averages 14.2 min.\n7. It is up 56.1%.", GROUNDING) is False


def test_a_number_starting_a_line_without_a_list_marker_is_still_a_claim():
    assert verify_numeric_claims("22.9 minutes late on route 12.", GROUNDING) is False
    assert verify_numeric_claims("1.5 minutes late on route 12.", GROUNDING) is False
