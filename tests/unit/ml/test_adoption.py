from ml.adoption import AGENCY_WORSE, COVERAGE_OUT_OF_RANGE, SKILL_BELOW_MIN, Failure, judge_values


def test_exactly_the_minimum_skill_and_the_top_of_the_coverage_band_pass():
    verdict = judge_values(0.05, {8: 0.0, 9: 0.1}, 0.85)
    assert verdict.adopted and verdict.reasons == ()


def test_the_bottom_of_the_coverage_band_passes():
    assert judge_values(0.2, {8: 0.1}, 0.75).adopted


def test_skill_just_below_the_minimum_fails():
    verdict = judge_values(0.0499, {8: 0.1}, 0.8)
    assert not verdict.adopted and "skill" in verdict.reasons[0]


def test_coverage_outside_the_band_fails_either_way():
    assert not judge_values(0.1, {8: 0.1}, 0.8501).adopted
    assert not judge_values(0.1, {8: 0.1}, 0.7499).adopted


def test_one_agency_worse_than_b0_fails_and_is_named():
    verdict = judge_values(0.12, {8: 0.2, 54: -0.001}, 0.8)
    assert not verdict.adopted
    assert any("54" in reason for reason in verdict.reasons)


def test_no_shared_cells_means_no_verdict_in_the_models_favour():
    assert not judge_values(None, {}, 0.8).adopted


def test_each_failure_is_data_the_app_can_word_itself():
    verdict = judge_values(0.046, {8: 0.1, 54: -0.01, 9: -0.2}, 0.9)
    assert verdict.failures == (
        Failure(SKILL_BELOW_MIN, 0.046),
        Failure(AGENCY_WORSE, agencies=(9, 54)),
        Failure(COVERAGE_OUT_OF_RANGE, 0.9),
    )


def test_an_unmeasured_figure_fails_with_no_value():
    verdict = judge_values(None, {}, None)
    assert verdict.failures == (Failure(SKILL_BELOW_MIN, None), Failure(COVERAGE_OUT_OF_RANGE, None))


def test_the_verdict_keeps_the_figures_it_judged():
    verdict = judge_values(0.06, {8: 0.07, 11: None}, 0.8)
    assert verdict.adopted and verdict.failures == ()
    assert (verdict.skill, verdict.coverage) == (0.06, 0.8)
    assert dict(verdict.agency_skills) == {8: 0.07, 11: None}


def test_the_worded_reasons_are_unchanged():
    verdict = judge_values(0.046, {54: -0.01, 9: -0.2}, 0.9)
    assert verdict.reasons == (
        "overall skill against B0 is 4.6%, below 5%",
        "worse than B0 for agency 9, 54",
        "p10–p90 holds 90.0% of runs, outside 75%–85%",
    )


def test_min_model_days_lives_in_the_light_result_module():
    from ml.model_result import MIN_MODEL_DAYS

    assert MIN_MODEL_DAYS == 56
