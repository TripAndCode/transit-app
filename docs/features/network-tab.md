# Network tab

Cross-agency health board: every agency ranked by average delay over a
date range, so an operator (or a curious rider) can see how one agency
compares to the whole network at a glance.

## How a user reaches it

- Route: `/agencies/:agencyId/network`, registered in `frontend/src/main.tsx`
  (`React.lazy`-loaded). A legacy bare `/network` bookmark still works via
  `frontend/src/routes/networkRedirect.tsx: RedirectNetworkToAgencyNetwork`,
  which forwards to the current agency's `/agencies/{id}/network` (Network
  was promoted from a standalone route into the sidebar's uniform per-agency
  nav — see the comment in `frontend/src/main.tsx`).
- Sidebar nav link: `frontend/src/components/Sidebar.tsx` (`nav.network`
  i18n key, labeled "Agencies").
- Top-level component: `frontend/src/tabs/NetworkTab.tsx`. Unlike every other
  tab, it does **not** use the shared `TabFilterBar`/`useRangeContext`
  dow/service/time_band/route filters — only a plain `from`/`to` date-range
  pair (whole-agency comparison, per the endpoint's own docstring).

What the user sees/does:

- **Eyebrow + title + help text**, and a collapsible "how to read this"
  `<details>` explaining each column (avg delay, on-time %, samples, feed
  health, freshness, coverage).
- **From/to date pickers** — plain HTML `<input type="date">` elements,
  independent of the shared range context.
- **Ranked agency card list** — one card per agency, sorted worst-avg-delay
  first (server-side order), each showing: rank, agency name (links to that
  agency's Overview tab, carrying the current date range), avg delay (color
  by `delayColor()`) + on-time %, a relative delay bar (scaled to the
  worst agency in the list), sample count, and two optional flags: a red dot
  when `clamp_pct` (implausible/clamped readings) is ≥1%, and a "stale" badge
  when the feed hasn't reported recently. The current agency's own card is
  visually highlighted with a "YOU" badge.

## Request path

| Frontend hook (`frontend/src/api/hooks.ts`) | Endpoint | Data source |
|---|---|---|
| `useNetworkSummary(ctx)` (only `ctx.from`/`ctx.to` are used) | `GET /api/network/summary?from=...&to=...` (`api/routers/network.py: network_summary`, not scoped under `/api/{agency_id}` like every other endpoint in this doc set) | `pipeline/reports/network.py: compute_network_summary()` — `samples` (deduped observation count) and the avg-delay/on-time figures come from Postgres `agg_route_daily_dist`; `raw_samples`/`clamp_count` (feed health) come from `agg_feed_health`. Freshness (`is_stale`) compares each agency's max `agg_route_daily_dist` date against a live ClickHouse `max_captured_at_before` probe per agency (one indexed read each) — a missing live timestamp is treated as "not stale" rather than failing, mirroring `today_route_summary`'s own freshness try/except in `api/routers/map.py`. |

## Key files

**Frontend**

| File | Role |
|---|---|
| `frontend/src/tabs/NetworkTab.tsx` | Network tab: date pickers, ranked card list rendering |
| `frontend/src/routes/networkRedirect.tsx` | Legacy bare `/network` → `/agencies/{currentAgencyId}/network` redirect |
| `frontend/src/styles/tokens.ts` | `delayColor()` — shared warm-ramp coloring used by the delay value/bar |
| `frontend/src/api/hooks.ts` | `useNetworkSummary` |

**Backend**

| File | Role |
|---|---|
| `api/routers/network.py` | `GET /api/network/summary` |
| `pipeline/reports/network.py` | `compute_network_summary()` |
| `api/clickhouse.py` | `max_captured_at_before_by_agency` — the per-agency live-freshness probe |
| `pipeline/analyze.py` | Builds `agg_route_daily_dist` and `agg_feed_health` — the aggregates this tab reads |

## How to verify manually

**Automated tests:**

- Backend: `tests/api/test_network.py`.
- Frontend: `frontend/src/tabs/NetworkTab.test.tsx`,
  `frontend/src/routes/networkRedirect.test.tsx`.

**Manual click-through** (`make serve` + `make frontend-dev`):

1. `make bootstrap && make serve` (+ `make frontend-dev`). Load and analyze
   data for at least two agencies to see a meaningful ranking:
   `make fetch-ingest` (or `ingest_live` + `make load_static`) then
   `make analyze` per agency — a single agency still renders (one card),
   just without a comparison.
2. Click "Agencies" in the sidebar → URL `/agencies/:agencyId/network`.
3. Expect a ranked card list, worst-avg-delay first, with the current
   agency's card visually highlighted and tagged "YOU".
4. Click another agency's name — expect navigation to that agency's
   Overview tab, carrying the same date range in the URL query string.
5. Change the from/to date pickers — expect the whole list to refetch and
   re-rank (note: this does **not** honor the dow/service/time_band/route
   filters used elsewhere, by design).
6. If any agency has a clamp rate ≥1% or a stale feed, expect the red dot /
   "stale" badge to render on that card; hover the badge for its tooltip
   text.
7. Visit the bare `/network` URL directly — expect an immediate redirect to
   `/agencies/{lastKnownAgencyId}/network`.

## i18n

- Frontend strings live under the `network.*` namespace in
  `frontend/src/i18n/locales/{ja,en}.json` (key parity CI-linted via
  `npm run lint:i18n`), plus `nav.network` / `nav.network_subtitle` for the
  sidebar entry and the shared `common.range_separator` string used in the
  coverage line.
