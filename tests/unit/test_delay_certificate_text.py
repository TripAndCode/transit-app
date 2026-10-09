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


def test_csv_footnotes_add_the_cut_only_when_rows_were_capped():
    assert format_delay_certificate_footnotes(300, shown=2, total=2) == ["遅延300秒超の便のみを掲載しています。"]
    assert format_delay_certificate_footnotes(300, shown=2, total=1234) == [
        "遅延300秒超の便のみを掲載しています。",
        "該当1234件のうち、日付順の先頭2件のみを掲載しています。残りは期間を絞って出力してください。",
    ]
