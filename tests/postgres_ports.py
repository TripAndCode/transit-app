"""Every port a Postgres connection string can reach, for the test-DB guard.

The suite connects through two drivers that read one URL differently, so there
is no single port to vet. libpq (psycopg2) lets a `port` query parameter
override the authority's port and reads PGPORT only when the string names no
port at all. asyncpg prefers the authority, reads the query's `port` and `host`
only when the authority names no host, takes a port out of a `host:port` query
value or PGHOST, and gives PGPORT to every host left without a port.
`candidate_ports` returns the union of both readings, so refusing any dev port
among them holds under either driver.

Not covered: a port supplied by a service file (`service=`, PGSERVICE).
"""

import re
from collections.abc import Mapping
from urllib.parse import parse_qs, unquote, urlparse

import psycopg2
from psycopg2.extensions import parse_dsn

_DEFAULT_PORT = 5432
_IPV6_HOSTSPEC = re.compile(r"\[[^\]]+\](?::([0-9]+))?")


def candidate_ports(url: str, env: Mapping[str, str]) -> set[int]:
    """The ports libpq or asyncpg could connect ``url`` to, with ``env`` as the
    process environment.

    Raises ``ValueError`` for a port that cannot be read as a number, or a
    string libpq cannot parse, so the caller refuses rather than guesses.
    """
    return _libpq_ports(url, env) | _asyncpg_ports(url, env)


def _port(text: str) -> int:
    # int() accepts everything libpq's strtol-based parse does (surrounding
    # whitespace, a sign, leading zeros), so a loosely written port still
    # compares equal to the dev port it names.
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"unreadable Postgres port {text!r}") from None


def _libpq_ports(url: str, env: Mapping[str, str]) -> set[int]:
    try:
        options = parse_dsn(url)
    except psycopg2.Error as exc:
        raise ValueError(f"libpq cannot parse the connection string: {exc}") from None
    spec = options.get("port")
    if spec is None:
        spec = env.get("PGPORT", "")
    # libpq uses its compiled-in default for an empty entry.
    return {_port(entry) if entry else _DEFAULT_PORT for entry in spec.split(",")}


def _asyncpg_ports(url: str, env: Mapping[str, str]) -> set[int]:
    parsed = urlparse(url)
    if parsed.scheme not in {"postgresql", "postgres"}:
        return set()  # asyncpg refuses any other string before connecting.
    query = {}
    if parsed.query:
        query = {key: values[-1] for key, values in parse_qs(parsed.query, strict_parsing=True).items()}
    auth, at, hostspec = parsed.netloc.partition("@")
    if not at:
        hostspec = auth
    if hostspec:
        return _hostlist_ports(hostspec, env, unquote_port=True)
    if query.get("port"):
        return {_port(entry) for entry in query["port"].split(",")}
    for hostlist in (query.get("host"), env.get("PGHOST")):
        if hostlist:
            return _hostlist_ports(hostlist, env, unquote_port=False)
    return _pgport(env)


def _hostlist_ports(hostlist: str, env: Mapping[str, str], *, unquote_port: bool) -> set[int]:
    ports: set[int] = set()
    for hostspec in hostlist.split(","):
        if hostspec.startswith("/"):
            explicit = ""
        elif hostspec.startswith("["):
            match = _IPV6_HOSTSPEC.match(hostspec)
            if match is None:
                raise ValueError(f"unreadable IPv6 host {hostspec!r}")
            explicit = match.group(1) or ""
        else:
            explicit = hostspec.partition(":")[2]
        if explicit:
            ports.add(_port(unquote(explicit) if unquote_port else explicit))
        else:
            ports |= _pgport(env)
    return ports


def _pgport(env: Mapping[str, str]) -> set[int]:
    spec = env.get("PGPORT")
    if not spec:
        return {_DEFAULT_PORT}
    return {_port(entry) for entry in spec.split(",")}
