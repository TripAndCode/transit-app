"""The API's daily prune enforces every period the privacy policy states."""

import asyncio

from api import retention


class _RecordingPool:
    def __init__(self, fail_on: str | None = None):
        self.executed: list[str] = []
        self._fail_on = fail_on

    async def execute(self, sql):
        self.executed.append(sql)
        if self._fail_on and self._fail_on in sql:
            raise ConnectionError("db down")
        return "DELETE 0"


def test_one_failing_statement_does_not_stop_the_others():
    pool = _RecordingPool(fail_on="login_events")
    asyncio.run(retention.prune_once(pool))
    assert len(pool.executed) == 5
    assert any("sessions" in sql for sql in pool.executed)


def test_daily_prune_covers_every_table_with_a_stated_period():
    pool = _RecordingPool()
    asyncio.run(retention.prune_once(pool))
    tables = {sql.split()[2] for sql in pool.executed}
    assert tables == {"user_activity_daily", "login_events", "sessions", "ask_query_log", "admin_audit"}
