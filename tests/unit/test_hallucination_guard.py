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


def test_japanese_date_of_a_grounded_day_passes():
    grounding = {"series": [{"date": "2026-09-05", "avg_min": 3.2}]}
    assert verify_numeric_claims("9月5日が最も遅れ（平均3.2分）", grounding) is True
    assert verify_numeric_claims("2026年9月5日が最も遅れ", grounding) is True


def test_japanese_date_of_an_ungrounded_day_still_fails():
    grounding = {"series": [{"date": "2026-09-05", "avg_min": 3.2}]}
    assert verify_numeric_claims("9月6日が最も遅れ", grounding) is False
    assert verify_numeric_claims("2025年9月5日が最も遅れ", grounding) is False


def test_percentage_of_a_grounded_rate_passes():
    grounding = {"time_adjustment_rate": 0.35}
    assert verify_numeric_claims("時刻調整の割合は35%です", grounding) is True
    assert verify_numeric_claims("時刻調整の割合は35％です", grounding) is True


def test_percentage_of_an_ungrounded_rate_still_fails():
    grounding = {"time_adjustment_rate": 0.35}
    assert verify_numeric_claims("時刻調整の割合は40%です", grounding) is False
    # A rate's scaled form is only a percentage's, not a bare number's.
    assert verify_numeric_claims("時刻調整は35回です", grounding) is False


def test_percentage_matches_only_a_grounded_fraction():
    # A grounded count of 3 or 1 is not a rate, so 300% or 100% traces to nothing.
    assert verify_numeric_claims("定時率は300%です", {"trips": 3}) is False
    assert verify_numeric_claims("定時率は100%です", {"context": "- 1 | 2 | 7 | 0.35"}) is False


def test_percentage_sign_may_follow_a_space():
    assert verify_numeric_claims("The adjustment rate is 35 %.", {"time_adjustment_rate": 0.35}) is True


def test_japanese_date_of_an_ungrounded_day_is_checked_as_its_numbers():
    # The question names the day as 9/6, which grounds its 9 and 6 as numbers.
    grounding = {"context": 'Series:\n- {"date": "2026-09-05", "avg_min": 3.2}', "question": "9/6はどうでしたか"}
    assert verify_numeric_claims("9月6日のデータは表にありません", grounding) is True
    assert verify_numeric_claims("9月7日のデータは表にありません", grounding) is False


def test_japanese_date_named_only_in_the_echoed_question_is_grounded():
    # The follow-up's grounding is its context block plus the user's question.
    grounding = {
        "context": 'Series:\n- {"date": "2026-09-05", "avg_min": 3.2}',
        "question": "9月6日はどうでしたか",
    }
    assert verify_numeric_claims("9月6日のデータは表にありません", grounding) is True
    assert verify_numeric_claims("9月7日のデータは表にありません", grounding) is False


def test_a_year_less_grounded_date_does_not_ground_a_wrong_year():
    grounding = {"context": 'Series:\n- {"date": "2026-09-05", "avg_min": 3.2}', "question": "9月5日はどうでしたか"}
    assert verify_numeric_claims("2026年9月5日が最も遅れ", grounding) is True
    assert verify_numeric_claims("2025年9月5日が最も遅れ", grounding) is False


def test_a_japanese_date_is_not_read_out_of_a_longer_number():
    grounding = {"series": [{"date": "2026-09-05", "avg_min": 1.0}]}
    assert verify_numeric_claims("12026年9月5日のデータです", grounding) is False


def test_a_japanese_date_is_not_read_out_of_a_decimal():
    grounding = {"series": [{"date": "2026-09-05", "avg_min": 3.0}]}
    assert verify_numeric_claims("3.9月5日のデータです。", grounding) is False


def test_a_short_year_does_not_leave_its_digits_as_a_separate_claim():
    grounding = {"context": 'Series:\n- {"date": "2026-09-05", "avg_min": 25.0}', "question": "どうでしたか"}
    assert verify_numeric_claims("25年9月5日が最も遅れ", grounding) is False


def test_stripping_a_grounded_date_leaves_its_neighbours_read_as_before():
    grounding = {"context": 'Series:\n- {"date": "2026-09-05", "avg_min": 3.2}', "question": "9月5日から7日まで"}
    # A day range keeps its 7 positive, as it reads without the date stripped.
    assert verify_numeric_claims("9月5日-7日の平均は3.2分", grounding) is True
    # A percent sign beyond a stripped date is not attached to the number before it.
    rated = {"series": [{"date": "2026-09-05", "avg_min": 3.2}], "time_adjustment_rate": 0.35}
    assert verify_numeric_claims("35 9月5日 %", rated) is False
