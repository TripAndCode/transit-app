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


# With no grounding at all there is nothing to verify against, so only a
# quantified metric claim counts as a fabrication. Bare digits on that path are
# route codes and day counts the system prompt itself tells the model to name.


def test_ungrounded_route_code_is_not_a_claim():
    reply = "天気データはありません。代わりに『22171の平日と土日祝の比較』が答えられます"
    assert verify_numeric_claims(reply, {}) is True


def test_ungrounded_period_suggestion_is_not_a_claim():
    assert verify_numeric_claims("直近2週間の傾向なら答えられます。", {}) is True
    assert verify_numeric_claims("I can compare the last 3 days instead.", {}) is True


def test_ungrounded_suggestion_count_is_not_a_claim():
    assert verify_numeric_claims("答えられる質問を3件挙げます。", {}) is True


def test_ungrounded_minute_claim_is_rejected():
    assert verify_numeric_claims("平均遅延は約14.2分です。", {}) is False
    assert verify_numeric_claims("Buses run about every 999 minutes off-peak.", {}) is False


def test_ungrounded_percentage_claim_is_rejected():
    assert verify_numeric_claims("定時率は56%です。", {}) is False
    assert verify_numeric_claims("On-time rate is 56.1 %.", {}) is False


def test_ungrounded_second_claim_is_rejected():
    assert verify_numeric_claims("平均は30秒の遅れです。", {}) is False


def test_grounded_path_still_checks_every_number():
    """Narrowing applies only to the ungrounded path — with grounding present,
    an unexplained bare number is still a fabrication."""
    assert verify_numeric_claims("Route 99999 is the worst.", {"route_code": "22171"}) is False
    assert verify_numeric_claims("Route 22171 is the worst.", {"route_code": "22171"}) is True


def test_ungrounded_spelled_out_percentage_is_rejected():
    """パーセント is the ordinary prose form of %, so it is the same claim."""
    assert verify_numeric_claims("定時率は56パーセントです。", {}) is False
    assert verify_numeric_claims("On-time rate is 56 per cent.", {}) is False


def test_ungrounded_wari_is_rejected_but_not_a_discount():
    assert verify_numeric_claims("定時率は8割です。", {}) is False
    # 割引/割合 are a fare discount and the word "proportion", not "N tenths".
    assert verify_numeric_claims("運賃は3割引です。", {}) is True
