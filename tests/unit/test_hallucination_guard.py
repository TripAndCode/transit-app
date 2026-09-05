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
