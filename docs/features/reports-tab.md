# Reports tab

Printable period summary: a daily mean-delay trend chart and a route ranking
table over the shared filter range, filtered CSV downloads, a share-link
copy button, browser print/PDF, and browser-local saved-analysis bookmarks —
plus a link out to the full Analysis tab for deeper reports.

## How a user reaches it

- Route: `/agencies/:agencyId/reports`, registered in `frontend/src/main.tsx`
  (`React.lazy`-loaded). This is distinct from
  `/agencies/:agencyId/reports/:reportType`, which redirects to the Analysis
  tab (see `docs/features/analysis-tab.md`) — only the bare `/reports` URL
  (no `:reportType`) renders this tab.
- Sidebar nav link: `frontend/src/components/Sidebar.tsx`'s
  `SIDEBAR_NAV_ITEMS` (`reports` entry, labeled from the `design` i18n
  namespace's `reports` key — "Reports" / "レポート").
- Top-level component: `frontend/src/tabs/ReportsHomeTab.tsx` — owns which
  of the two views ("summary" vs. "saved", via the `?view=saved` search
  param) is shown.

What the user sees/does:

- **Summary view** (default):
  - **Filter bar** — `frontend/src/components/TabFilterBar.tsx`.
  - **Trend section** — a daily mean-delay line chart
    (`frontend/src/components/analysis/PeriodChart.tsx`) with a CSV download.
  - **Routes-to-check section** — a ranked pattern table (route, days,
    mean delay, samples), each row linking to the Route analysis tab
    (`/agencies/:agencyId/route-analysis`) pre-filtered to that route (and
    service type, when the row is weekday/weekend-specific), plus its own
    CSV download.
  - A **definitions** `<details>` showing the active filter window and a
    `DefinitionMetaBlock` (on-time/late tolerance, measurement point, dedup
    rule, exclusion threshold) from the trend report's `definition` field,
    plus a **"Detailed reports →"** link to
    `/agencies/:agencyId/analysis/trend` carrying the same filter.
  - A **footer** with a combined CSV download (trend + ranking rows) and a
    "copy share link" button that copies the current URL with its filter
    query string.
  - A **print** button in the header (`window.print()`) for browser
    print/PDF.
- **Saved view** (`?view=saved`) — lists this browser's saved analyses for
  the current agency (`frontend/src/components/analysis/savedAnalyses.ts`),
  each linking back into Route analysis with its saved filter query; an
  `EmptyState` shows when none exist for this agency. Bookmarks are
  browser-local filter snapshots, not immutable historical results —
  opening one re-queries the API with today's data under that filter.

## Request path

| Frontend hook (`frontend/src/api/hooks.ts`) | Endpoint | Data source |
|---|---|---|
| `useReport(agencyId, "trend", ctx)` | `GET /api/{agency_id}/reports/trend` (`api/routers/reports.py: get_report`) | `pipeline/reports/rankings.py: compute_trend_series()` — precomputed `agg_daily_trend` by default, falling back to a live ClickHouse scan for a `time_band`-narrowed request. Same function the Analysis tab's `trend` report and the Ask tab's `trend` tool call. |
| `useReport(agencyId, "ranking", ctx)` | `GET /api/{agency_id}/reports/ranking` (`api/routers/reports.py: get_report`) | `pipeline/reports/rankings.py: compute_ranking()` — same `agg_*`-fast-path-with-live-fallback pattern as `trend`. |
| `useAgencies()` (only used for the current agency's display name in the section heading) | `GET /api/agencies` (`api/routers/agencies.py`, not scoped under `/api/{agency_id}`) | Static agency metadata from Postgres. |

Saved-analysis reads/writes and the CSV/share-link/print actions are entirely
client-side — they read `trend`/`ranking` data already in memory rather than
issuing their own requests.

## Key files

**Frontend**

| File | Role |
|---|---|
| `frontend/src/tabs/ReportsHomeTab.tsx` | Tab shell: summary/saved view state, trend + ranking sections, CSV/share/print actions |
| `frontend/src/components/analysis/PeriodChart.tsx` | Daily mean-delay trend chart |
| `frontend/src/components/analysis/savedAnalyses.ts` | Browser-local saved-analysis read/write/delete |
| `frontend/src/components/analysis/csv.ts` | `downloadCsv()` shared by every analysis/report screen |
| `frontend/src/components/DefinitionMetaBlock.tsx` | Renders the on-time/late definition metadata block |
| `frontend/src/components/TabFilterBar.tsx` | Shared dow/service/time_band/route filter UI |
| `frontend/src/api/hooks.ts` | `useReport`, `useAgencies` |

**Backend**

| File | Role |
|---|---|
| `api/routers/reports.py` | `GET /reports/{report_type}` (shared with the Analysis tab) |
| `pipeline/reports/rankings.py` | `compute_trend_series()`, `compute_ranking()` |
| `pipeline/reports/definition.py` | Definition metadata resolved into every report's `definition` field |
| `pipeline/analyze.py` | Builds `agg_daily_trend` and the ranking aggregates this tab reads |

## How to verify manually

**Automated tests:**

- Frontend: `frontend/src/components/analysis/workflows.test.tsx` (renders
  `ReportsHomeTab` and `RouteAnalysisTab` together on their real routes,
  including the routes-to-check → route-analysis link).
- Backend: `tests/api/test_reports.py`, `tests/unit/test_reports_rounding.py`
  (both shared with the Analysis tab, since both read the same
  `/reports/{report_type}` endpoint).

**Manual click-through** (`make serve` + `make frontend-dev`):

1. `make bootstrap && make serve` (+ `make frontend-dev`). Load and analyze
   data first: `make fetch-ingest` (or `ingest_live` + `make load_static`),
   then `make analyze` for the agency.
2. Click "Reports" in the sidebar → URL `/agencies/:agencyId/reports`;
   expect the trend chart and routes-to-check table to populate (or the
   empty state with no data).
3. Change the filter bar's date range / dow / time_band / route selection —
   expect both sections to refetch.
4. Click a route in the routes-to-check table — expect navigation to Route
   analysis pre-filtered to that route.
5. Click "Detailed reports →" — expect navigation to
   `/agencies/:agencyId/analysis/trend` carrying the same filter.
6. Click each CSV download button — expect a file per section plus one
   combined file from the footer button.
7. Click "Create share link" — expect a clipboard-copy confirmation; paste
   the URL in a new tab and confirm the same filter loads.
8. Click "Print / Save PDF" — expect the browser print dialog.
9. Switch to the "Saved analyses" view — expect any bookmarks saved from
   Route analysis for this agency to appear, each linking back with its
   saved filter; delete one and confirm it disappears.

## i18n

- Frontend strings come from the `design` i18n namespace
  (`frontend/src/i18n/design.ts`, loaded via `useTranslation("design")`), not
  the default `translation` namespace's `frontend/src/i18n/locales/{ja,en}.json`
  files. `design.ts` holds its own `ja`/`en` objects directly in the TS
  module; `npm run lint:i18n`'s key-parity check and
  `npm run lint:i18n-strings` both explicitly skip this file, so a new
  `design.ts` key needs its `ja`/`en` pair kept in sync by hand.
- Every report response carries the same `definition` block described in
  `docs/features/analysis-tab.md`'s i18n section; this tab renders it through
  the shared `DefinitionMetaBlock` component rather than duplicating that
  logic.
