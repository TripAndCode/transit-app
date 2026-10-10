from ml.adoption import judge_values


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
