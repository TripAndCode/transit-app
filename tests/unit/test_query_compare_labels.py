"""The Ask compare summary names the two day types its dimension=dow branch
splits by: the agency's 平日 days against its 土日祝 days, holidays included."""

from pipeline.query.tools import _summary


def test_dow_compare_summary_names_weekends_and_holidays_in_both_languages():
    assert _summary("compare_summary_dow", lang="ja") == "平日 vs 土日祝 遅延比較"
    assert _summary("compare_summary_dow", lang="en") == "Weekday vs weekend/holiday delay comparison"
