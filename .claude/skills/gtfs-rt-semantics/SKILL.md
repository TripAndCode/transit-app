---
name: gtfs-rt-semantics
description: What an observation in this repo means — a feed-predicted delay, filed by service day in Japan time, reduced to the latest poll per stop event, clamped, matched to a timetable version — and the invariants every figure rests on. Use before changing ingest, analyze, an aggregate, a report metric, date or service-day handling, or any text that labels a number.
---

# GTFS-RT semantics — transit-app

The defects that recur in this layer are not slips of code but of meaning: a date
bucketed on the wrong clock, one stop event counted once per poll, a 25:30 departure
dropped, a cancelled trip counted as late, a prediction labelled as a measurement. Each
rule below names the one place that implements it. Read that code before changing
anything that depends on it, and keep the rule in that one place.

## What a row in `updates` is

- One row per (agency, poll, trip, stop_sequence): a TripUpdate stop-time prediction,
  captured at `captured_at` (UTC) from feed file `file_name`. `dep_delay`/`arr_delay`
  are **the operator's bus-location vendor's prediction at that poll**, not a measured
  departure. No VehiclePositions are ingested and no vehicle id is stored, so nothing
  here can produce a vehicle- or driver-level figure. Schema: `db/clickhouse/schema.sql`.
- The latest poll per stop event is the closest the data gets to what happened:
  `pipeline/db.py::build_dedup_ch_sql` keeps `argMax(..., (captured_at, file_name))`
  per stop event, which `pipeline/reports/definition.py` names `DEDUP_RULE`.
  `build_stop_visits_ch_sql` pairs the latest arrival with the latest departure for
  dwell and run time. A metric that reads `updates` without one of those two, or an
  equivalent latest-poll `argMax` per stop event, counts polls, not events.
  `agg_stop_routes` is the one deliberate exception (`postgres-perf` explains why).
- Clamp: an observation beyond `MAX_PLAUSIBLE_DELAY_SEC` (`pipeline/db.py`) is excluded
  inside the dedup, because a frozen feed otherwise skews every mean. Every
  `DefinitionMeta` carries that threshold as `exclusion_threshold_sec`. The dedup also
  drops a reading received more than `MAX_PLAUSIBLE_DELAY_SEC +
  LISTED_AFTER_DEPARTURE_SEC` after the scheduled departure (`timely_reading_ch`);
  `DEDUP_RULE`'s user-facing text states that sum.
- `static_version_id` names the static GTFS zip the row's `static_join` matched;
  NULL on a join miss and for the regex strategy, which joins nothing. A join from an
  observation to `static_*` reads the version the observation matched, not "the
  current timetable": `pipeline/analyze.py` rebuilds the dates that follow a reloaded
  timetable for exactly that reason.
- `schedule_relationship_trip`/`_stop` are nullable and sparse. A CANCELED trip or a
  SKIPPED stop is "not run", never "late". `pipeline/db.py::withdrawn_ch` is the
  shared predicate (the dedup, feed quality, transfers);
  `pipeline/agg_sql.py::service_delivered_ch_sql` counts delivered service, and its
  `coalesce(…, -1)` before `argMax` is load-bearing. Other readers take the columns
  directly (`pipeline/static_archive.py`, the strategies, the network and replay
  routers), so a change to how a withdrawal is read starts with a search for the
  column names, not for these helpers.
- `service_type` and `route_code` are NULL in real feeds. An aggregate that neither
  COALESCEs nor filters them drops rows silently (`postgres-perf`).

## Clocks

- `captured_at` is UTC; every date bucket, filter and "today" is Japan time.
  `api/range.py::jst_today` is the civil calendar and
  `api/range.py::current_service_date` the service day in progress (the day before,
  until the cutover hour); the API and analyze database sessions pin `Asia/Tokyo`.
  Never the server's local date.
- The **service day** turns over at `SERVICE_DAY_CUTOVER_HOURS` (04:00 JST), not at
  midnight: `pipeline/service_day.py` (`service_date_ch`, `service_day_offset`,
  `service_day_start_utc`). Bucketing on the calendar date splits a 23:40 → 00:30 trip
  across two dates and double-counts its tail. Never `toDate(captured_at)`: a reading
  buckets by `service_date_ch(...)`, and a stop event (the dedup, every aggregate built
  on it) is filed by `filed_date_ch`, which moves a reading near the cutover to its
  trip's service day (`pipeline/static_archive.py::FILED_DATE_SQL`).
- GTFS `departure_time` reaches past 24:00 (`25:30:00`). `scheduled_time` is NULL for
  `hour >= 24`; `scheduled_sec` holds the value
  (`pipeline/strategies/_time.py::parse_departure_time`). An hour-of-day bucket parsed
  from `scheduled_time` silently drops every extended-hour trip.
- History ends at the last closed JST day (`api/range.py::last_closed_jst_day`):
  `updates` receives a day only once `pipeline/promote.py` moves it out of the live
  table, and the after-midnight tail of the newest service day arrives with the next
  promotion. A test that seeds `NOW()` is wrong for most of every day; use
  `tests/fixtures/clock.py` (`transit-app-gotchas` has the full rule).

## On-time

- On-time windows are the presets in `pipeline/reports/rankings.py::ON_TIME_PRESETS`,
  as (early, late) tolerances. `DEFAULT_PRESET` is `standard_m1_p5`, (60 s, 300 s), the
  window operators publish; `legacy_60s`, (none, 60 s), counts every early departure
  as on time. `frontend/src/api/onTimeDefault.json` mirrors the default. A window
  `pipeline/histogram.py::exact_window` accepts (late 60 s or 300 s, early none or on a
  bucket edge) reads analyze's exact columns; any other reads the approximate
  histogram.
- A ranked group needs observations before its percentiles mean anything:
  `MIN_GROUP_SAMPLES` is `compute_ranking`'s floor and `RANKING_MIN_SAMPLES` the
  higher one the Reports ranking applies (both in `pipeline/reports/rankings.py`).
  A figure on fewer says so rather than ranking (`docs/features/audience.md`).
- `DefinitionMeta` (`pipeline/reports/definition.py`) travels with every on-time or
  late figure and every export: preset, tolerances, clamp, measurement point, dedup
  rule. A new report, card or export that shows such a figure without it breaks the
  comparability promise the council material depends on. Render its text through the
  helpers there, not through new literals.

## Rules that are product decisions, not schema details

- No vehicle- or driver-level metric. Storing a vehicle id would make one possible
  and is a decision for the user, recorded in `docs/features/audience.md` first.
- A number shown to a user says what it is: a predicted delay at the last poll, not an
  actual one. Labels, footnotes and CSV preambles come from `DefinitionMeta`.
- A defect here gets its regression through the synthetic fixture
  (`tests/fixtures/synthetic_gtfs.py`), per `AGENTS.md`'s Tests section.
