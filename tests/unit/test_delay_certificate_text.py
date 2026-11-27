"""The delay record's summary line and CSV footnotes must give the true count
of qualifying trips, say so when only the first rows are listed, and say the
list is the service's reference document, not the operator's certificate.
The summary is footnoted with what each value is, the method version it was
drawn under, the timetables in force and where each day's observations came from."""

from datetime import date, datetime, timezone

import pytest

from pipeline.agg_registry import METHOD_VERSION
from pipeline.query.formatter import format_delay_certificate_footnotes, format_delay_certificate_text
from pipeline.reports.definition import METHODOLOGY_URLS, SourceMix, TimetableVersion, resolve_definition_meta

_ROWS = [("青森市バス", "R1", "平日", "2026-06-20", "10:00:00", "10:05:01", 301)] * 2
_DEFINITION = resolve_definition_meta("delay_certificate", None, None, live=True)
_BASIS_AND_LINK = {
    "ja": "※値の根拠: 事業者のフィードが停留所ごとに最後に配信した遅延の予測値から算出（通過の観測値ではない）\n"
    f"※算出方法の説明: {METHODOLOGY_URLS['ja']}",
    "en": "* Value basis: computed from the delays the operator's feed last predicted for each stop "
    f"(not observed passing times)\n* Methodology: {METHODOLOGY_URLS['en']}",
}
_STAMP = {
    "ja": f"{_BASIS_AND_LINK['ja']}\n※算出方法: {METHOD_VERSION}、集計日: 対象外（ライブ集計）",
    "en": f"{_BASIS_AND_LINK['en']}\n* Method version: {METHOD_VERSION}, aggregates built: not applicable (live scan)",
}


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
        (
            "ja",
            f"遅延300秒超の便: 2件\n{_BASIS_AND_LINK['ja']}\n※算出方法: {METHOD_VERSION}、集計日: 2026-11-06\n"
            "※時刻表: gtfs_static_20260601（2026-06-01取得、内容ハッシュ a1b2c3d4）1日間（全1日間）\n"
            "※観測データ: ライブ 1日間（全1日間）。アーカイブの日は収集サーバーがその日に取得した全データ、"
            "ライブの日はアプリがリアルタイムで受け取った一部のデータのみ",
        ),
        (
            "en",
            f"2 trip(s) exceeded the 300s delay threshold.\n{_BASIS_AND_LINK['en']}\n"
            f"* Method version: {METHOD_VERSION}, aggregates built: 2026-11-06\n"
            "* Timetable: gtfs_static_20260601 (fetched 2026-06-01, content hash a1b2c3d4): 1 day (of 1 day)\n"
            "* Observations: live 1 day (of 1 day). Archive days hold every poll the collector made that day; "
            "live days hold only the part the app received in real time",
        ),
    ],
)
def test_the_summary_is_footnoted_with_whatever_stamp_the_definition_carries(locale, expected):
    """The record is a live scan, so its router marks it live; the footnote
    renders an aggregate definition's build day the same way, the
    timetables its router named and the source of the day's observations."""
    built = resolve_definition_meta("delay_certificate", None, None).model_copy(
        update={
            "built_at": datetime(2026, 11, 6, 1, tzinfo=timezone.utc),
            "timetable_versions": [
                TimetableVersion(
                    agency_id=1,
                    name="gtfs_static_20260601",
                    fetched_on=date(2026, 6, 1),
                    days=1,
                    content_hash="a1b2c3d4",
                )
            ],
            "source_mix": [SourceMix(agency_id=1, archive_days=0, live_days=1, none_days=0)],
        }
    )
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
