"""The dwell/running-time report's text, which the CSV export also carries.

An empty decomposition states its true cause: a feed whose arrival times
cannot be taken in, a check that found the needed fields missing, a check
that has lapsed, or one that never ran. None of these says the feed sends
departures only, which no feed this app ingests has been shown to do.
"""

import pytest

from pipeline.query.formatter import format_dwell_run_text


def _unavailable(reason, last_checked=None):
    return {
        "available": False,
        "time_band_supported": True,
        "routes": [],
        "unavailable_reason": reason,
        "last_checked": last_checked,
    }


@pytest.mark.parametrize(
    "reason, last_checked, locale, expected",
    [
        (
            "feed_unsupported",
            None,
            "ja",
            "この事業者のフィードからは到着時刻を取り込めないため、滞留・走行時間の内訳は表示できません。",
        ),
        (
            "feed_unsupported",
            None,
            "en",
            "Arrival times can't be taken from this agency's feed, so the dwell/running time breakdown can't be shown.",
        ),
        (
            "probe_refuted",
            "2026-09-11",
            "ja",
            "最終確認（2026-09-11）で、この事業者のフィードが内訳に必要な項目を送っていないと分かったため、"
            "滞留・走行時間の内訳は表示していません。",
        ),
        (
            "probe_refuted",
            "2026-09-11",
            "en",
            "The last check (2026-09-11) found that this agency's feed doesn't send the fields the breakdown needs, "
            "so the dwell/running time breakdown isn't shown.",
        ),
        (
            "probe_expired",
            "2026-09-11",
            "ja",
            "到着時刻データの確認期限が切れているため、再確認が済むまで滞留・走行時間の内訳は表示していません"
            "（最終確認：2026-09-11）。",
        ),
        (
            "probe_expired",
            "2026-09-11",
            "en",
            "The check of this feed's arrival-time data has expired, so the dwell/running time breakdown isn't "
            "shown until it is re-checked (last checked 2026-09-11).",
        ),
        (
            "never_probed",
            None,
            "ja",
            "この事業者のフィードが到着時刻を送っているかをまだ確認していないため、"
            "滞留・走行時間の内訳は表示していません。",
        ),
        (
            "never_probed",
            None,
            "en",
            "Whether this agency's feed sends arrival times hasn't been checked yet, so the dwell/running time "
            "breakdown isn't shown.",
        ),
    ],
)
def test_each_reason_reads_its_own_true_sentence(reason, last_checked, locale, expected):
    assert format_dwell_run_text(_unavailable(reason, last_checked), locale=locale) == expected


@pytest.mark.parametrize("locale", ["ja", "en"])
def test_a_payload_without_a_reason_reads_as_never_checked(locale):
    payload = {"available": False, "time_band_supported": True, "routes": []}
    assert format_dwell_run_text(payload, locale=locale) == format_dwell_run_text(
        _unavailable("never_probed"), locale=locale
    )


def test_no_reason_claims_the_feed_sends_departures_only():
    for reason in ("feed_unsupported", "probe_refuted", "probe_expired", "never_probed"):
        text = format_dwell_run_text(_unavailable(reason, "2026-09-11"), locale="ja")
        assert "出発時刻のみ" not in text
        assert "送信しない" not in text
