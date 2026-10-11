from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from pipeline.service_day import SERVICE_DAY_CUTOVER_HOURS, service_date_ch, service_date_of


def _jst(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=ZoneInfo("Asia/Tokyo"))


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        # An after-midnight stop belongs to the service day still running.
        (_jst(2026, 3, 7, 0, 15), date(2026, 3, 6)),
        (_jst(2026, 3, 7, 3, 59), date(2026, 3, 6)),
        # The cutover itself opens the next service day.
        (_jst(2026, 3, 7, SERVICE_DAY_CUTOVER_HOURS, 0), date(2026, 3, 7)),
        (_jst(2026, 3, 7, 12, 0), date(2026, 3, 7)),
        (_jst(2026, 3, 7, 23, 50), date(2026, 3, 7)),
    ],
)
def test_service_date_of_moves_the_day_boundary_to_the_cutover(moment, expected):
    assert service_date_of(moment) == expected


def test_service_date_of_reads_a_utc_instant_in_japan_time():
    # 2026-03-06 15:30 UTC is 2026-03-07 00:30 JST: still service day the 6th.
    assert service_date_of(datetime(2026, 3, 6, 15, 30, tzinfo=timezone.utc)) == date(2026, 3, 6)


def test_the_sql_fragment_shifts_by_the_same_cutover_in_japan_time():
    sql = service_date_ch("u.captured_at")
    assert sql == f"toDate(u.captured_at - INTERVAL {SERVICE_DAY_CUTOVER_HOURS} HOUR, 'Asia/Tokyo')"
    assert service_date_ch() == f"toDate(captured_at - INTERVAL {SERVICE_DAY_CUTOVER_HOURS} HOUR, 'Asia/Tokyo')"
