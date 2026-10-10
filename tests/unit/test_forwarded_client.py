"""``api.middleware.forwarded``: the client address is the X-Forwarded-For
entry the outermost trusted proxy appended, never one a client wrote."""

import pytest

from api.middleware.forwarded import ForwardedClientMiddleware, forwarded_hops


async def _seen(hops: int, headers: list[tuple[bytes, bytes]], *, kind: str = "http") -> dict:
    seen: dict = {}

    async def app(scope, receive, send):
        seen.update(client=scope["client"], scheme=scope["scheme"])

    scope = {
        "type": kind,
        "client": ("10.0.0.9", 4321),
        "scheme": "http" if kind == "http" else "ws",
        "headers": headers,
    }
    await ForwardedClientMiddleware(app, hops=hops)(scope, None, None)
    return seen


async def test_reads_the_entry_the_edge_appended_not_the_one_the_client_wrote():
    seen = await _seen(1, [(b"x-forwarded-for", b"6.6.6.6, 203.0.113.7")])
    assert seen["client"] == ("203.0.113.7", 0)


async def test_an_edge_that_replaces_the_header_reads_the_same():
    seen = await _seen(1, [(b"x-forwarded-for", b"203.0.113.7")])
    assert seen["client"] == ("203.0.113.7", 0)


async def test_counts_hops_from_the_right_across_repeated_header_lines():
    headers = [(b"x-forwarded-for", b"6.6.6.6, 203.0.113.7"), (b"x-forwarded-for", b"198.51.100.2")]
    seen = await _seen(2, headers)
    assert seen["client"] == ("203.0.113.7", 0)


async def test_keeps_the_peer_when_fewer_entries_than_proxies():
    seen = await _seen(2, [(b"x-forwarded-for", b"203.0.113.7")])
    assert seen["client"] == ("10.0.0.9", 4321)


async def test_keeps_the_peer_without_the_header():
    seen = await _seen(1, [])
    assert seen["client"] == ("10.0.0.9", 4321)


async def test_ignores_the_headers_with_no_proxy_in_front():
    seen = await _seen(0, [(b"x-forwarded-for", b"6.6.6.6"), (b"x-forwarded-proto", b"https")])
    assert seen == {"client": ("10.0.0.9", 4321), "scheme": "http"}


async def test_takes_the_scheme_the_edge_set():
    assert (await _seen(1, [(b"x-forwarded-proto", b"https")]))["scheme"] == "https"
    assert (await _seen(1, [(b"x-forwarded-proto", b"https")], kind="websocket"))["scheme"] == "wss"
    assert (await _seen(1, [(b"x-forwarded-proto", b"gopher")]))["scheme"] == "http"


@pytest.mark.parametrize("raw", [None, "", " "])
def test_hops_default_to_none(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("FORWARDED_HOPS", raising=False)
    else:
        monkeypatch.setenv("FORWARDED_HOPS", raw)
    assert forwarded_hops() == 0


@pytest.mark.parametrize("raw", ["-1", "one", "1.5"])
def test_a_hop_count_that_is_not_a_whole_number_fails_startup(monkeypatch, raw):
    monkeypatch.setenv("FORWARDED_HOPS", raw)
    with pytest.raises(ValueError):
        forwarded_hops()
