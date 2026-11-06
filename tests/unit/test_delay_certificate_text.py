"""The delay record's summary line and CSV footnotes must give the true count
of qualifying trips, say so when only the first rows are listed, and say the
list is the service's reference document, not the operator's certificate.
The summary is footnoted with the method version it was drawn under."""

from datetime import datetime, timezone

import pytest

from pipeline.agg_registry import METHOD_VERSION
from pipeline.query.formatter import format_delay_certificate_footnotes, format_delay_certificate_text
from pipeline.reports.definition import resolve_definition_meta

_ROWS = [("青森市バス", "R1", "平日", "2026-06-20", "10:00:00", "10:05:01", 301)] * 2
_DEFINITION = resolve_definition_meta("delay_certificate", None, None)
_STAMP = {"ja": f"※算出方法: {METHOD_VERSION}", "en": f"* Method version: {METHOD_VERSION}"}


def _text(rows, total, locale, **kwargs):
    return format_delay_certificate_text(rows, 300, total=total, locale=locale, definition=_DEFINITION, **kwargs)


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("ja", "遅延300秒超の便: 2件"),
        ("en", "2 trip(s) exceeded the 300s delay threshold."),
    ],
)
def test_summary_counts_every_row_when_none_were_cut(locale, expected):
    assert _text(_ROWS, 2, locale) == f"{expected}\n{_STAMP[locale]}"


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
    assert _text(_ROWS, 1234, locale) == f"{expected}\n{_STAMP[locale]}"


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
    assert _text([], 0, locale, observed=observed) == f"{expected}\n{_STAMP[locale]}"


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("ja", f"遅延300秒超の便: 2件\n※算出方法: {METHOD_VERSION}（2026-11-06 集計）"),
        (
            "en",
            "2 trip(s) exceeded the 300s delay threshold.\n"
            f"* Method version: {METHOD_VERSION} (aggregates built 2026-11-06)",
        ),
    ],
)
def test_the_summary_is_footnoted_with_the_definitions_stamp(locale, expected):
    """The record reads no aggregate, so its router leaves the build day out;
    the footnote still renders whatever the definition carries."""
    built = _DEFINITION.model_copy(update={"built_at": datetime(2026, 11, 6, 1, tzinfo=timezone.utc)})
    assert format_delay_certificate_text(_ROWS, 300, total=2, locale=locale, definition=built) == expected


_REFERENCE_JA = (
    "この一覧は、事業者が公開しているリアルタイムデータをもとに本サービスが作成した参考資料です。"
    "事業者が発行する遅延証明書ではありません。"
)


def test_csv_footnotes_add_the_cut_only_when_rows_were_capped():
    assert format_delay_certificate_footnotes(300, shown=2, total=2) == [
        _REFERENCE_JA,
        "遅延300秒超の便のみを掲載しています。",
    ]
    assert format_delay_certificate_footnotes(300, shown=2, total=1234) == [
        _REFERENCE_JA,
        "遅延300秒超の便のみを掲載しています。",
        "該当1234件のうち、日付順の先頭2件のみを掲載しています。残りは期間を絞って出力してください。",
    ]


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("ja", _REFERENCE_JA),
        (
            "en",
            "This list is a reference document this service made from the real-time data the operator publishes. "
            "It is not a delay certificate issued by the operator.",
        ),
    ],
)
def test_csv_footnotes_open_by_saying_the_list_is_no_certificate(locale, expected):
    assert format_delay_certificate_footnotes(300, shown=0, total=0, locale=locale)[0] == expected
