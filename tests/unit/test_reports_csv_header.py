from api.routers.reports import _REPORT_CSV_COLUMNS


def test_compare_ranking_weekend_header_names_the_weekend_and_holiday_day_type():
    """compare_ranking splits days by the agency's day types, so its weekend
    column is 土日祝: a weekday public holiday is counted there."""
    assert _REPORT_CSV_COLUMNS["compare_ranking"][1:3] == ["平日(分)", "土日祝(分)"]


def test_dow_reports_name_their_day_type_column():
    """The dow reports' third column is the day type the row was counted over."""
    for report in ("dow_weekday", "dow_weekend"):
        assert _REPORT_CSV_COLUMNS[report][2] == "日種別"
