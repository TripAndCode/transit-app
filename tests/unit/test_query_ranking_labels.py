"""The Ask summary's name for each ranking metric matches the report it comes
from: the over-5-min ranking counts departures strictly over 300 s, so it
says "over", in both languages."""

from pipeline.query.tools import _summary


def test_over_5_min_ranking_says_over_in_both_languages():
    assert _summary("label_ranking_late5", lang="ja") == "5分超の遅延件数"
    assert _summary("label_ranking_late5", lang="en") == "Count of delays over 5 min"
