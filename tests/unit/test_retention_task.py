"""The API's daily prune enforces every period the privacy policy states."""

import asyncio

from api import retention


def _table(sql: str) -> str:
    words = sql.split()
    return words[1] if words[0] == "UPDATE" else words[2]


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
    assert len(pool.executed) == 7
    assert any("sessions" in sql for sql in pool.executed)


def test_daily_prune_covers_every_table_with_a_stated_period():
    pool = _RecordingPool()
    asyncio.run(retention.prune_once(pool))
    tables = {_table(sql) for sql in pool.executed}
    assert tables == {
        "user_activity_daily",
        "login_events",
        "sessions",
        "ask_query_log",
        "ask_intent_cache",
        "admin_audit",
    }


def test_daily_prune_clears_what_an_admin_action_left_on_login_history():
    pool = _RecordingPool()
    asyncio.run(retention.prune_once(pool))
    assert any(sql.startswith("UPDATE login_events SET ip = NULL, user_agent = NULL") for sql in pool.executed)
