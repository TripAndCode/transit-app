"""Unit tests for the pure digest renderer (no DB)."""

from datetime import date

from pipeline.digest.models import AgencySection, DigestData, Mover
from pipeline.digest.render import _DIGEST_LOCALES, render_digest


def _sample_data():
    movers = [
        Mover("44372", 8.0, 3.0, 5.0, "anomaly", False),
        Mover("12", 6.0, 3.0, 3.0, "watch", True),
    ]
    return DigestData(
        target_day=date(2026, 4, 2),
        network_avg_delay_min=5.0,
        sections=[
            AgencySection(1, "広島電鉄", True, 4.0, 2.5, 1.5, movers, 3400, 12, False),
            AgencySection(2, "広島バス", False, None, None, None, [], 0, 0, True),
        ],
    )


def test_locale_key_parity():
    keys = {k for (k, _lang) in _DIGEST_LOCALES}
    for k in keys:
        assert (k, "ja") in _DIGEST_LOCALES, f"missing ja for {k}"
        assert (k, "en") in _DIGEST_LOCALES, f"missing en for {k}"


def test_render_ja_contains_core_parts():
    out = render_digest(_sample_data(), "ja")
    assert "2026-04-02" in out
    assert "広島電鉄" in out
    assert "44372" in out
    assert "広島バス" in out
    assert "データなし" in out
    assert "※少数サンプル" in out
    assert "鮮度警告" in out


def test_render_en_switches_language():
    out = render_digest(_sample_data(), "en")
    assert "Daily digest" in out
    assert "No data for" in out
    assert "(low sample)" in out
    assert "Freshness: aggregates lagging" in out


def test_movers_capped_and_no_data_network():
    empty = DigestData(target_day=date(2026, 4, 2), network_avg_delay_min=None, sections=[])
    out = render_digest(empty, "ja")
    assert "データがありません" in out


def test_staleness_unknown_does_not_render_as_fresh():
    """staleness_known=False (the ClickHouse-probe-failed degrade) must render
    a distinct "could not be checked" line, never fall through to the
    affirmative "all agencies current" footer — every section's is_stale is
    False either way (probe never ran), so this flag is the only signal."""
    data = DigestData(
        target_day=date(2026, 4, 2),
        network_avg_delay_min=5.0,
        sections=[AgencySection(1, "広島電鉄", True, 4.0, 2.5, 1.5, [], 3400, 12, False)],
        staleness_known=False,
    )
    out_ja = render_digest(data, "ja")
    assert "全事業者最新" not in out_ja
    assert "鮮度警告" not in out_ja
    assert "確認できませんでした" in out_ja

    out_en = render_digest(data, "en")
    assert "all agencies current" not in out_en
    assert "aggregates lagging" not in out_en
    assert "could not be checked" in out_en


def test_staleness_known_true_still_renders_fresh_or_stale():
    """Sanity: the normal (staleness_known=True, default) path is untouched —
    still renders the plain fresh/stale footer."""
    fresh = DigestData(
        target_day=date(2026, 4, 2),
        network_avg_delay_min=5.0,
        sections=[AgencySection(1, "広島電鉄", True, 4.0, 2.5, 1.5, [], 3400, 12, False)],
    )
    assert "全事業者最新" in render_digest(fresh, "ja")

    stale = DigestData(
        target_day=date(2026, 4, 2),
        network_avg_delay_min=5.0,
        sections=[AgencySection(1, "広島電鉄", True, 4.0, 2.5, 1.5, [], 3400, 12, True)],
    )
    assert "鮮度警告" in render_digest(stale, "ja")
