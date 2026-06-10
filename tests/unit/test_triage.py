from api.triage import LOW_CONFIDENCE_SAMPLES, classify_route


def test_no_baseline_returns_no_baseline_bucket():
    bucket, deviation, low = classify_route(
        avg_delay_sec=300, baseline_avg_sec=None, baseline_p90_sec=None, samples=100
    )
    assert bucket == "no_baseline"
    assert deviation is None
    assert low is False


def test_normal_when_below_midpoint():
    # baseline avg=120s, p90=360s, midpoint=240s; today=180s -> normal
    bucket, deviation, low = classify_route(180, 120.0, 360.0, 100)
    assert bucket == "normal"
    assert deviation == 60  # 180 - 120
    assert low is False


def test_watch_when_above_midpoint_below_p90():
    # midpoint=240s; today=300s (<=360 p90) -> watch
    bucket, _, _ = classify_route(300, 120.0, 360.0, 100)
    assert bucket == "watch"


def test_anomaly_when_above_p90():
    bucket, deviation, _ = classify_route(420, 120.0, 360.0, 100)
    assert bucket == "anomaly"
    assert deviation == 300


def test_low_confidence_caps_anomaly_at_watch():
    # would be anomaly, but only 5 samples (< 30) -> downgraded to watch + flagged
    bucket, _, low = classify_route(420, 120.0, 360.0, samples=5)
    assert bucket == "watch"
    assert low is True


def test_low_confidence_does_not_promote_normal():
    bucket, _, low = classify_route(180, 120.0, 360.0, samples=5)
    assert bucket == "normal"
    assert low is True


def test_low_confidence_threshold_boundary():
    # exactly 30 samples is NOT low confidence
    _, _, low = classify_route(180, 120.0, 360.0, samples=LOW_CONFIDENCE_SAMPLES)
    assert low is False
