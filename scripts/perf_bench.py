"""Bench the hot read endpoints against a running localhost server.

Usage:
    poetry run python scripts/perf_bench.py --agency 1 [--base-url http://localhost:8000]
        [--runs 10] [--cookie "session=..."] [--markdown]

Per target: POST /api/debug/perf/reset (clears perf registry + lru caches)
-> 1 cold request -> N warm requests -> report cold ms / warm p50 / warm p95.
Read-only traffic; safe against the dev DB. Requires PERF_DEBUG_ENABLED
(default on) on the server.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time

import httpx

# {agency}/{route}/{range} are filled in main(). {range} = "from=..&to=.." for
# RangeCtx endpoints; "today/*" map endpoints use the latest captured day and
# take no range. {route} is auto-discovered from /routes.
_R = "{range}"
TARGETS = [
    ("overview", f"/api/{{agency}}/overview/summary?{_R}"),
    ("report.ranking", f"/api/{{agency}}/reports/ranking?{_R}"),
    ("report.ranking_best", f"/api/{{agency}}/reports/ranking_best?{_R}"),
    ("report.on_time", f"/api/{{agency}}/reports/on_time?{_R}"),
    ("report.worst_5min", f"/api/{{agency}}/reports/worst_5min?{_R}"),
    ("report.trend", f"/api/{{agency}}/reports/trend?{_R}"),
    ("report.compare_ranking", f"/api/{{agency}}/reports/compare_ranking?{_R}"),
    ("report.dow_weekend", f"/api/{{agency}}/reports/dow_weekend?{_R}"),
    ("report.dow_weekday", f"/api/{{agency}}/reports/dow_weekday?{_R}"),
    ("dashboard.heatmap", f"/api/{{agency}}/ask/dashboard/heatmap?{_R}"),
    ("dashboard.anomalies", f"/api/{{agency}}/ask/dashboard/anomalies?{_R}"),
    ("dashboard.movers", f"/api/{{agency}}/ask/dashboard/movers?{_R}"),
    # --- map tab ---
    ("map.heatmap", f"/api/{{agency}}/delays/heatmap?{_R}"),
    ("map.live", "/api/{agency}/delays/live"),
    ("map.route_summary", "/api/{agency}/today/route-summary"),
    ("map.route_shape", f"/api/{{agency}}/route-shape?route={{route}}&{_R}"),
    ("map.route_trips", "/api/{agency}/today/route/{route}/trips"),
    ("map.stop_profile", "/api/{agency}/today/route/{route}/stop-profile"),
    # --- static / ask autocomplete ---
    ("static.routes", "/api/{agency}/routes"),
    ("static.stops", "/api/{agency}/stops"),
    ("ask.suggest", "/api/{agency}/ask/suggest?q=&limit=8"),
]


def _discover_route(client: httpx.Client, agency: int) -> str:
    """Return a real route_code for the agency (first one), for route-scoped targets."""
    r = client.get(f"/api/{agency}/routes")
    r.raise_for_status()
    rows = r.json()
    return str(rows[0].get("route_code") or rows[0].get("code") or "") if rows else ""


def _request_ms(client: httpx.Client, url: str) -> float:
    """GET ``url`` and return wall-clock duration in milliseconds (raises on non-2xx)."""
    t0 = time.perf_counter()
    r = client.get(url)
    r.raise_for_status()
    return (time.perf_counter() - t0) * 1000.0


def main() -> int:
    """Run the bench against every TARGET and print a cold/p50/p95 table."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--agency", type=int, default=1)
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--cookie", default=None, help="optional Cookie header for authed servers")
    ap.add_argument("--from-date", default="2026-03-11", help="RangeCtx 'from' (server clamps to MAX_RANGE_DAYS)")
    ap.add_argument("--to-date", default="2026-06-09", help="RangeCtx 'to'")
    ap.add_argument("--timeout", type=float, default=45.0, help="per-request cap (s); over this → recorded as ceiling")
    ap.add_argument("--markdown", action="store_true", help="emit a GFM table")
    args = ap.parse_args()

    headers = {"Cookie": args.cookie} if args.cookie else {}
    rng = f"from={args.from_date}&to={args.to_date}"
    rows: list[tuple[str, float, float, float]] = []

    cap_ms = args.timeout * 1000.0
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=args.timeout) as client:
        route = _discover_route(client, args.agency)
        for name, path in TARGETS:
            url = path.format(agency=args.agency, route=route, range=rng)
            if "{route}" in path and not route:
                print(f"SKIP {name}: no route discovered", file=sys.stderr)
                continue
            reset = client.post("/api/debug/perf/reset")
            if reset.status_code != 200:
                print(
                    f"FATAL: reset returned {reset.status_code} — is the server up with PERF_DEBUG_ENABLED?",
                    file=sys.stderr,
                )
                return 1
            try:
                cold = _request_ms(client, url)
                warm = sorted(_request_ms(client, url) for _ in range(args.runs))
            except httpx.HTTPStatusError as exc:
                print(f"SKIP {name}: HTTP {exc.response.status_code}", file=sys.stderr)
                continue
            except httpx.TimeoutException:
                # over the cap → record as a ceiling so it ranks worst, keep going
                print(f"TIMEOUT {name}: >{args.timeout:.0f}s", file=sys.stderr)
                rows.append((name, cap_ms, cap_ms, cap_ms))
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
