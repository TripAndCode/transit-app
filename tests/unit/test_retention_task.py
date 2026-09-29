"""The API's daily personal-data prune keeps going when one statement fails."""

import asyncio

from api import retention


def test_one_failing_statement_does_not_stop_the_others():
    executed: list[str] = []

    class _Pool:
        async def execute(self, sql):
            executed.append(sql)
            if "login_events" in sql:
                raise ConnectionError("db down")
            return "DELETE 0"

    asyncio.run(retention.prune_once(_Pool()))
    assert len(executed) == 3
    assert any("sessions" in sql for sql in executed)
