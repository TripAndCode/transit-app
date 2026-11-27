"""One Postgres connection and an on-demand ClickHouse client, closed together.

For the long-lived processes that open both per unit of work (the collector
push endpoint, the standalone poller): the connection is pinned to JST like
every other, and the ClickHouse client is only built when first used, so work
that needs Postgres alone goes ahead while ClickHouse is down.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class Stores:
    def __init__(self, conn, db_url: str, send_receive_timeout: int | None):
        self.conn = conn
        self.db_url = db_url
        self._send_receive_timeout = send_receive_timeout
        self._ch: Any = None

    @property
    def ch(self) -> Any:
        if self._ch is None:
            from pipeline.clickhouse import get_client

            self._ch = get_client(send_receive_timeout=self._send_receive_timeout)
        return self._ch

    def close(self) -> None:
        # Nested so a Postgres close that raises still closes the ClickHouse
        # client and its HTTP pool, which a long-lived process would leak.
        try:
            self.conn.close()
        finally:
            if self._ch is not None:
                self._ch.close()


@contextmanager
def open_stores(db_url: str | None = None, *, send_receive_timeout: int | None = None) -> Iterator[Stores]:
    """Connect to *db_url* (default ``DATABASE_URL``) in autocommit, pinned to
    JST; the ClickHouse client is built on first use of ``.ch``."""
    import psycopg2

    from pipeline.db import DEFAULT_DATABASE_URL

    db_url = db_url or os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL
    conn = psycopg2.connect(db_url)
    stores = Stores(conn, db_url, send_receive_timeout)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'Asia/Tokyo'")
        yield stores
    finally:
        stores.close()
