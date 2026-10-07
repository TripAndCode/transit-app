"""Seeding for the heatmap's ``stop_clusters`` table in API tests."""

from pipeline.stop_clusters import stop_clusters_statements


async def rebuild_stop_clusters(conn, agency_id: int) -> None:
    """The heatmap reads stop_clusters, which load_static maintains; a test
    that seeds static_stops directly rebuilds it the same way. *conn* is an
    asyncpg connection or pool."""
    delete, insert = stop_clusters_statements("$1")
    await conn.execute(delete, agency_id)
    await conn.execute(insert, agency_id)
