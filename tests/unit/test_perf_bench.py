"""scripts/perf_bench.py against a stub server: the reset it needs and how it reports a refusal."""

from __future__ import annotations

import sys

import httpx
import pytest

from scripts import perf_bench


def _run(monkeypatch: pytest.MonkeyPatch, handler, *argv: str) -> int:
    real_client = httpx.Client

    def _client(**kwargs: object) -> httpx.Client:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(perf_bench.httpx, "Client", _client)
    monkeypatch.setattr(sys, "argv", ["perf_bench.py", "--runs", "1", *argv])
    return perf_bench.main()


def test_the_reset_carries_the_servers_origin_as_the_csrf_guard_requires(monkeypatch: pytest.MonkeyPatch):
    resets: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/debug/perf/reset":
            resets.append(request)
            return httpx.Response(200 if request.headers.get("origin") else 403, json={"status": "reset"})
        if request.url.path == "/api/debug/perf":
            return httpx.Response(200, json={"ops": {}, "caches": {}, "pool": {}})
        return httpx.Response(200, json={})

    assert _run(monkeypatch, handler, "--base-url", "http://localhost:8000/", "--cookie", "session=x") == 0
    assert resets and all(r.headers["origin"] == "http://localhost:8000" for r in resets)


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "admin session"), (403, "admin session"), (404, "PERF_DEBUG_ENABLED")],
)
def test_a_refused_reset_names_its_real_cause(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], status: int, expected: str
):
    assert _run(monkeypatch, lambda _request: httpx.Response(status)) == 1
    assert expected in capsys.readouterr().err
