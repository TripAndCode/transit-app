"""``aconn`` must leave an empty database behind even when the test broke its connection.

The two tests run in definition order: the first commits a row and closes
its own connection, so the fixture's teardown cannot reuse it; the second
asserts the row did not survive into it.
"""

_AGENCY_NAME = "aconn-reset-probe"


async def test_a_test_that_closes_its_own_connection_leaves_a_row(aconn):
    await aconn.execute(
        "INSERT INTO agencies (agency_name, feed_url) VALUES ($1, $2)",
        _AGENCY_NAME,
        "http://example.com/aconn-reset-probe.pb",
    )
    await aconn.close()


async def test_the_next_test_starts_without_that_row(aconn):
    leaked = await aconn.fetchval("SELECT count(*) FROM agencies WHERE agency_name = $1", _AGENCY_NAME)
    assert leaked == 0

