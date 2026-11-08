# How delay analysis works

An overview of what this app actually does with transit delays. For the architecture
details see `README.md`; for AI-assist conventions see `AGENTS.md`.

## The raw data

We poll each agency's real-time feed (GTFS-RT) on a schedule. The readings the app
receives land in `updates_live`, and each closed day is promoted into the `updates`
table. One row means: **"this trip, at this stop, was N seconds late."**

That number — `dep_delay` (seconds) = actual departure − scheduled departure — comes
straight from the feed. **We don't compute the delay; the feed gives it to us. We
collect and summarize it.**

## The processing (`pipeline/analyze.py`)

After ingest, `analyze` turns the raw readings into summary tables:

1. **dedup** — the same trip + stop + day is observed many times; keep only the latest
   reading per trip-stop-day.
2. **clamp** — drop impossible values (more than 120 minutes late), which come from
   frozen/stale feeds re-sending the same stuck estimate, not from real delays.
3. **aggregate** — precompute averages and p90 sliced different ways into `agg_*` tables:
   - `agg_route_hour` — route × service type (weekday / Saturday / Sunday-holiday) ×
     scheduled departure time → average delay
   - `agg_route_hour_dow` — route × day-of-week × scheduled hour → average delay
   - `agg_route_daily` — route × day → average delay
   - `agg_route_stats` — route overall (avg, p50, p90, on-time %)
   - …and per-stop / per-hour / feed-health variants

## Method versions

Every report, CSV export and council document names the method version its figures were
made with, and the day their aggregates last changed: a run that rebuilds nothing leaves
that day alone. The version covers every
`agg_*` table's logic version and the plausibility clamp. A change to any of them is a
new version: `METHOD_VERSIONS` in `pipeline/agg_registry.py` records what each version
set, `tests/unit/method_versions.json` pins each released one, and
`tests/unit/test_method_version.py` keeps them, the registry and this table in step. A
rebuild replaces the stored aggregates, so figures exported under an earlier version
may differ from today's for the same period. Figures from a live scan of the
observations, such as a time-band view or the delay record, name the version but no
build day.

| Version | What changed | Tables |
|---|---|---|
| `2026.11.0` | Baseline. The latest reading per trip, stop and service day counts, and readings more than 120 minutes off are dropped. Stop, dwell/run and headway rows join the timetable each reading's feed version archived, and each day's long gaps are measured against that timetable's own headway. | All |
| `2026.11.1` | A departure scheduled at 24:00 or later counts under its service day's hour 24, 25, … and in the time band of its clock time (24:30 is late night), where the hourly and per-stop banded tables had left it out. | `agg_hour_daily`, `agg_route_hour`, `agg_route_hour_dow`, `agg_stop_daily`, `agg_route_stop_daily`, `route_delay_counts` |
| `2026.11.2` | Each day's feed-health row also counts its observations by ingest source: from the collector's archive, or from the live path. The stamp files each day under the source most of its observations came from. No delay or on-time figure changes. | `agg_feed_health` |
| `2026.11.3` | Each reading is filed under the timetable in force on its service day: the newest loaded timetable whose feed dates, or else calendar, cover that day. Until now a reading took whichever timetable was loaded when it arrived, so a timetable published ahead of its first day was applied to the rest of the day before. Such readings now take the timetable in force for their scheduled time, day type and stop, and for their route where the feed named none. A day no known timetable covers keeps the timetable its readings joined. | `agg_daily_trend`, `agg_route_daily`, `agg_route_daily_dist`, `agg_hour_daily`, `agg_stop_daily`, `agg_route_stop_daily`, `agg_route_stats`, `agg_route_hour`, `agg_route_hour_dow`, `agg_stop_routes`, `agg_route_daily_dwell_run`, `agg_route_headway_daily`, `agg_schedule_revision_daily`, `route_delay_counts`, `stop_route_days` |

## What counts as on time

A departure is on time when it leaves no more than 1 minute early and no more than 5
minutes late: the `standard_m1_p5` preset, the window operators publish. Every on-time
figure uses it unless its request names another: the reports, the Routes table, the
council report, the scope summary's tolerance curve, the performance standards, the
agencies board and its comparability gate, and the Ask tools. `DEFAULT_PRESET` in
`pipeline/reports/rankings.py` is the one place that says so;
`frontend/src/api/onTimeDefault.json` mirrors it for the screens, and
`tests/unit/test_on_time_default.py` keeps the two in step.

- **The legacy definition**, `legacy_60s`, counts every early departure as on time and
  allows up to 1 minute late. Ask for it with `preset=legacy_60s`, with the scope's
  `early=any&late=60`, or on screen with 「早発も定時に含める」 and the 1-minute pill.
- **Other tolerances.** A request may name its own `late` and `early`, in seconds. Each
  one it leaves out is the default's (300 s late, 60 s early), and `early=any` counts
  every early departure. A window is named by its preset whenever it equals one, however
  the request spelt it, and `custom` otherwise.
- **Exact and estimated windows.** Both presets are read exactly from analyze's columns
  (`on_time_count`, `late5_count` and the delay histogram's leading buckets), as is any
  window with a late tolerance of 1 or 5 minutes and an early tolerance on a whole minute
  up to 5. Any other window is estimated from the delay histogram, within one minute.
- **The early-departure rate** counts departures more than the early tolerance early, or
  more than 1 minute early when every early departure is on time, so its threshold is 1
  minute under either preset. The count of departures more than 5 minutes late does not
  depend on the preset.
- **Ask's on-time ranking** uses the default unless the question names how late counts
  (「5分以内の定時率」); with such a threshold, every early departure counts as on time.

Choosing a preset changes no aggregate, so it is not a method version. A figure is
identified by its method version and its preset together, and the stamp shows both: the
CSV preamble's `プリセット=` (with the preset's plain name, such as
`standard_m1_p5（業界標準）`) and `算出方法=`, and the footnotes' on-time definition
(「定時の定義（業界標準）：…」) and method line.

## Serving

By default a report endpoint just **reads** an `agg_*` table rather than scanning the raw
`updates` table. That is why the default views stay fast regardless of how much raw data
has piled up. A request narrowed by a filter an aggregate cannot apply (such as a time
band) falls back to a live ClickHouse scan of `updates`, and the live/today views read
ClickHouse's `updates_live` directly. A few reads have no aggregate behind them and always
scan `updates`, such as a route page's stop-by-stop shape (`/route-shape`) and several
Ask tools (see `docs/features/ask-tab.md`). After a fresh ingest, `make analyze` (or
`make analyze-all`) rebuilds these tables.

## What "forecasting" would mean here

The baseline is **not** prediction with a trained model. It is **looking up an average we
already computed**:

> "Route X, on weekdays, in the 5 p.m. hour → historically +8 min on average"
> is that route's hour-17 rows for Monday–Friday in `agg_route_hour_dow`,
> pooled by sample count.

This is a *seasonal-naive baseline* (a.k.a. climatology): the future is assumed to
resemble the historical pattern for that route at that time. It is honest to call it
**"expected" / "typical" delay**, not a prediction — it cannot see disruptions, trend
drift, weather, or special events. The only fiddly parts are mapping a calendar date to
its service type (weekday / Saturday / Sunday-holiday) and bucketing a clock time into an
hour; the core is "look up one average."

A trained model's forecast can be laid over that baseline, but only behind the
`ml_forecast` flag (off by default): see "The 'expected delay' endpoints" below and
`docs/features/ml-forecast.md`.

## The "expected delay" endpoints

Two read endpoints are the concrete version of "look up an average", both computing their
baseline from the precomputed `agg_route_hour_dow` table (per route × service_type ×
day-of-week × scheduled hour):

- `GET /api/{agency_id}/forecast/heatmap?route=…` — the full day-of-week × hour grid for one
  route (the `route_forecast` report's per-route view).
- `GET /api/{agency_id}/forecast/overview` — pooled across all routes into a day × time-band
  grid, plus the worst window and a delay-ranked route list (the `route_forecast` report's
  agency-wide view).

Both report the sample-weighted mean (the exact pooled mean), never a percentile (a weighted
mean of per-bucket percentiles is not the pooled percentile).

When the `ml_forecast` flag is on, both also overlay the published ML forecast from
`agg_route_forecast_ml` on the cells it covers: each cell carries `source: "ml"` where the
forecast supplied its value and `source: "baseline"` elsewhere.
`docs/features/ml-forecast.md` covers how that forecast is trained and published.

**A "sample" (delay measurement)** = one run of a scheduled departure, measured at one stop,
on one day. A busy route accumulates thousands; it is not a count of trips.

The response always includes a `disclaimer` written for non-experts (no statistics jargon),
stating what the figure is drawn from and that it cannot see incidents, weather, or today's
conditions. When any cell comes from the ML forecast, the disclaimer says instead that some
cells are a forecast model's estimate and the rest are historical averages. **Any
visualization of this number must show that disclaimer.**
