"""Bench the hot read endpoints against a running localhost server.

Usage:
    poetry run python scripts/perf_bench.py --agency 1 [--base-url http://localhost:8000]
        [--runs 10] [--cookie "sid=<admin session id>"] [--markdown]

Per target: POST /api/debug/perf/reset (clears perf registry + lru caches)
-> 1 cold request -> N warm requests -> report cold ms / warm p50 / warm p95.
Read-only traffic; safe against the dev DB. Requires PERF_DEBUG_ENABLED on the
server (off by default), and ``--cookie`` must be an admin session: the reset
is admin-only and CSRF-guarded. The script sends ``--base-url``'s origin with
it, which the server accepts only when PUBLIC_BASE_URL or CORS_ORIGINS lists it.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from urllib.parse import urlsplit

import httpx

TARGETS = [
    ("overview", "/api/{agency}/overview/summary"),
    ("report.ranking", "/api/{agency}/reports/ranking"),
    ("report.ranking_best", "/api/{agency}/reports/ranking_best"),
    ("report.on_time", "/api/{agency}/reports/on_time"),
    ("report.worst_5min", "/api/{agency}/reports/worst_5min"),
    ("report.trend", "/api/{agency}/reports/trend"),
    ("report.compare_ranking", "/api/{agency}/reports/compare_ranking"),
    ("report.dow_weekend", "/api/{agency}/reports/dow_weekend"),
    ("report.dow_weekday", "/api/{agency}/reports/dow_weekday"),
    ("dashboard.heatmap", "/api/{agency}/ask/dashboard/heatmap"),
    ("dashboard.anomalies", "/api/{agency}/ask/dashboard/anomalies"),
    ("dashboard.movers", "/api/{agency}/ask/dashboard/movers"),
]


def _request_ms(client: httpx.Client, url: str) -> float:
    """GET ``url`` and return wall-clock duration in milliseconds (raises on non-2xx)."""
    t0 = time.perf_counter()
    r = client.get(url)
    r.raise_for_status()
    return (time.perf_counter() - t0) * 1000.0


def _origin(base_url: str) -> str:
    """The Origin a browser on ``base_url`` would send: scheme and host, no path."""
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}"


_ORIGIN_REFUSALS = frozenset({"origin required", "cross-origin request denied"})


def _reset_refusal(response: httpx.Response) -> str:
    """Why the reset was refused. A 403 comes from either the admin check or the
    origin check, so the server's ``detail`` tells the two apart."""
    if response.status_code == 404:
        return "the perf debug surface is off: set PERF_DEBUG_ENABLED=true on the server"
    try:
        body = response.json()
    except ValueError:
        body = None
    detail = body.get("detail") if isinstance(body, dict) else None
    if response.status_code == 403 and detail in _ORIGIN_REFUSALS:
        return "the server refused --base-url's origin: list it in PUBLIC_BASE_URL or CORS_ORIGINS"
    if response.status_code in (401, 403):
        return "the reset needs an admin session: pass --cookie with one"
    return "unexpected server error"


def main() -> int:
    """Run the bench against every TARGET and print a cold/p50/p95 table."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--agency", type=int, default=1)
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--cookie", default=None, help="Cookie header carrying an admin session (the reset needs one)")
    ap.add_argument("--markdown", action="store_true", help="emit a GFM table")
    args = ap.parse_args()

    headers = {"Cookie": args.cookie} if args.cookie else {}
    rows: list[tuple[str, float, float, float]] = []

    with httpx.Client(base_url=args.base_url, headers=headers, timeout=60.0) as client:
        for name, path in TARGETS:
            url = path.format(agency=args.agency)
            reset = client.post("/api/debug/perf/reset", headers={"Origin": _origin(args.base_url)})
            if reset.status_code != 200:
                reason = _reset_refusal(reset)
                print(f"FATAL: reset returned {reset.status_code} — {reason}", file=sys.stderr)
                return 1
            try:
                cold = _request_ms(client, url)
                warm = sorted(_request_ms(client, url) for _ in range(args.runs))
            except httpx.HTTPStatusError as exc:
                print(f"SKIP {name}: {exc.response.status_code}", file=sys.stderr)
                continue
            p50 = statistics.median(warm)
            p95 = warm[min(len(warm) - 1, round(0.95 * (len(warm) - 1)))]
            rows.append((name, cold, p50, p95))

        server_snap = client.get("/api/debug/perf").json()

    if args.markdown:
        print("| target | cold ms | warm p50 | warm p95 |")
        print("|---|---:|---:|---:|")
        for name, cold, p50, p95 in rows:
            print(f"| {name} | {cold:.0f} | {p50:.0f} | {p95:.0f} |")
    else:
        for name, cold, p50, p95 in rows:
            print(f"{name:28s} cold={cold:7.0f}ms  p50={p50:6.0f}ms  p95={p95:6.0f}ms")

    print("\nserver-side op labels (last target's window):")
    for label, st in server_snap.get("ops", {}).items():
        print(f"  {label:32s} n={st['count']:<5d} p50={st['p50_ms']:<8} p95={st['p95_ms']:<8} max={st['max_ms']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
