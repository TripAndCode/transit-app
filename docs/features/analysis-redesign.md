# Focused delay analysis — draft PR stack

1. Foundation: shared route / keito selection, period controls, safe CSV export,
   bilingual copy and responsive styles.
2. Overview: map-first current observations, bounded map, compact freshness,
   filtered CSV, and a link to investigate the selected keito.
3. Route analysis: stop delay chart, selected stop context, matched-stop
   previous-week comparison, filtered CSV and locally saved analysis links.
4. Reports and navigation: three main destinations (overview, route analysis,
   reports), period summary, saved analyses, CSV and browser print/PDF.

All PRs remain draft. CI is not a merge gate for this design review; run local
typechecking and focused behavior tests. Do not merge or deploy this stack.

## Data contracts

- A keito is the existing API `route_code`, not `service_type` (weekday calendar)
  or a direction. Group routes by the published `route_long_name` (falling back
  to the published short name and then id) with any leading 系統番号 token
  stripped first, since some feeds embed the pattern number directly in
  `route_long_name` (e.g. "14-5 共立ハイツ線") rather than in `route_short_name`.
- The shared `routes` URL parameter contains the exact selected keito codes.
  Keep that scope in queries, links, saved analyses and CSV exports.
- Existing stop data is **mean departure delay**, not median, exact vehicle GPS
  or a causal estimate. Route-shape chooses the dominant observed pattern.
  Never connect multiple patterns or unmatched stops as one trip.
- Live reception age and historical aggregation coverage are separate signals.
  A stale snapshot is historical observation, not evidence of current operation.
- Missing data is not zero delay. New designs must expose empty/error states.
- Saved analyses are filter bookmarks in this browser, not immutable reports.
- CSV contains the currently displayed data, UTF-8 BOM and formula-safe cells.
  Report downloads retain existing server-side filtering and definitions.

## Deferred

Causal attribution, what-if forecasts, account-synced saved reports, persistent
share links and precise vehicle-position playback require separate data work.
Legacy report URLs remain reachable but the large catalog is removed from the
primary navigation. Theme preferences remain supported.
