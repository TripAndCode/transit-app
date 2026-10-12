from api.routers.reports import _REPORT_CSV_COLUMNS


def test_compare_ranking_weekend_header_names_the_plain_sat_sun_split():
    """compare_ranking sums ISO days 6 and 7, so its weekend column is 土日 (Sat/Sun),
    not the 土日祝 timetable service type that also covers holidays."""
    header = _REPORT_CSV_COLUMNS["compare_ranking"]
    assert header[2] == "土日(分)"
    assert not any("土日祝" in col for col in header)
