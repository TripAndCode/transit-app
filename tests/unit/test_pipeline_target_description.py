"""`describe_target` renders a DATABASE_URL safely enough to log."""

import pytest

from gtfs_pipeline import describe_target


@pytest.mark.parametrize(
    "url,expected",
    [
        ("postgresql://transit:transit@localhost:5433/transit", "localhost:5433/transit"),
        ("postgresql://transit:transit@127.0.0.1:5543/transit", "127.0.0.1:5543/transit"),
        # No port: the default is the server's to choose, so claiming one here
        # would misreport where the run actually connected.
        ("postgresql://localhost/transit", "localhost/transit"),
        ("postgresql://user@db.internal:5432/transit_test", "db.internal:5432/transit_test"),
    ],
)
def test_renders_host_port_and_database(url, expected):
    assert describe_target(url) == expected


def test_port_zero_is_rendered_not_dropped():
    """`parsed.port` returns the int 0 for an explicit `:0`, which is falsy —
    a bare truthiness check would silently drop a real (if unusual) port."""
    assert describe_target("postgresql://host:0/db") == "host:0/db"


def test_omits_credentials_entirely():
    """Dropped, not masked — a mask still leaks the password's length."""
    rendered = describe_target("postgresql://admin:sup3r-s3cret@localhost:5433/transit")

    assert "sup3r-s3cret" not in rendered
    assert "admin" not in rendered
    assert "*" not in rendered
    assert rendered == "localhost:5433/transit"


def test_degrades_instead_of_raising_on_a_urlless_value():
    # This string reaches the logger before psycopg2 ever validates it, so it
    # must not be the thing that raises. Echoing the unparseable value back is
    # what makes a typo'd DATABASE_URL recognisable in the log.
    assert describe_target("not-a-url") == "?/not-a-url"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("postgresql://transit:transit@localhost:5544x/transit_test", "localhost:5544x/transit_test"),
        ("postgresql://host:abc/db", "host:abc/db"),
        ("postgresql://host:99999/db", "host:99999/db"),
    ],
)
def test_does_not_raise_on_an_unparseable_port(url, expected):
    """A typo'd port must not make this the thing that fails.

    `urlsplit(...).port` raises rather than returning None when the substring
    is not an in-range integer, and this runs before the connection attempt —
    so raising here would replace the driver's precise complaint about the URL
    with an unrelated traceback from the logging step. The bad text is echoed
    because it is the whole point of the line, and because the driver quotes it
    too.
    """
    assert describe_target(url) == expected


def test_a_netloc_without_userinfo_is_a_hostspec_not_a_credential():
    """`postgresql://admin:54321/db` is host `admin` port 54321 to libpq.

    A password only ever lives before an `@`, so there is nothing to withhold
    here — and withholding the port would lose the one field that distinguishes
    two Postgres instances on the same host.
    """
    assert describe_target("postgresql://admin:54321/transit") == "admin:54321/transit"


def test_userinfo_is_dropped_even_when_the_port_is_unparseable():
    """The fallback path must read the hostspec only, never the userinfo."""
    rendered = describe_target("postgresql://admin:sup3r-s3cret@localhost:badport/transit")

    assert "sup3r-s3cret" not in rendered
    assert "admin" not in rendered
    assert rendered == "localhost:badport/transit"


def test_does_not_raise_on_a_netloc_urlsplit_itself_rejects():
    """An unencoded `[` or `]` in a password trips `urlsplit`'s own IPv6-bracket
    check before this function gets a chance to parse anything — a real
    password can easily contain either character. This must still not be the
    thing that raises, and must still drop the credential.
    """
    rendered = describe_target("postgresql://admin:pa]ss@localhost:5432/transit")

    assert "pa]ss" not in rendered
    assert "admin" not in rendered
    assert rendered == "localhost:5432/transit"


def test_does_not_raise_on_an_unmatched_bracket_with_no_credentials():
    assert describe_target("postgresql://[::1/transit") == "postgresql://[::1/transit"
