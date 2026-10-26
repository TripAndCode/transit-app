"""The delay-certificate export's summary line and CSV footnotes must give the
true count of qualifying trips, and say so when only the first rows are
listed."""

import pytest

from pipeline.query.formatter import format_delay_certificate_footnotes, format_delay_certificate_text

_ROWS = [("青森市バス", "R1", "平日", "2026-06-20", "10:00:00", "10:05:01", 301)] * 2


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("ja", "遅延300秒超の便: 2件"),
        ("en", "2 trip(s) exceeded the 300s delay threshold."),
    ],
)
def test_summary_counts_every_row_when_none_were_cut(locale, expected):
    assert format_delay_certificate_text(_ROWS, 300, total=2, locale=locale) == expected


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("ja", "遅延300秒超の便: 1234件（日付順の先頭2件を表示。残りは期間を絞って確認できます）"),
        (
            "en",
            "1234 trip(s) exceeded the 300s delay threshold; the earliest 2 are listed. "
            "Narrow the date range to see the rest.",
        ),
    ],
)
def test_summary_states_the_total_and_the_cut_when_rows_were_capped(locale, expected):
    assert format_delay_certificate_text(_ROWS, 300, total=1234, locale=locale) == expected


@pytest.mark.parametrize(
    ("observed", "locale", "expected"),
    [
        (True, "ja", "遅延300秒超の便はありませんでした。"),
        (True, "en", "No trips exceeded the 300s delay threshold."),
        (False, "ja", "この期間・条件のリアルタイムデータはありません。"),
        (False, "en", "There is no live-feed data for this period and these conditions."),
        (None, "ja", "遅延300秒超の便は見つかりませんでした。データの有無は確認できませんでした。"),
        (
            None,
            "en",
            "No trip over the 300s delay threshold was found; "
            "whether the feed reported this period couldn't be checked.",
        ),
    ],
)
def test_an_empty_list_says_whether_the_feed_reported_the_scope(observed, locale, expected):
    """An empty list claims "no trip exceeded" only when the feed reported the
    scope, and says when that couldn't be checked."""
    assert format_delay_certificate_text([], 300, total=0, locale=locale, observed=observed) == expected


def test_csv_footnotes_add_the_cut_only_when_rows_were_capped():
    assert format_delay_certificate_footnotes(300, shown=2, total=2) == ["遅延300秒超の便のみを掲載しています。"]
    assert format_delay_certificate_footnotes(300, shown=2, total=1234) == [
        "遅延300秒超の便のみを掲載しています。",
        "該当1234件のうち、日付順の先頭2件のみを掲載しています。残りは期間を絞って出力してください。",
    ]
