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
   reading per trip-stop-day. When that latest report says the trip was cancelled or the
   stop skipped, the stop has no departure, however it was predicted before. A trip the
   feed cancels without listing any stop is stored as a cancellation of each stop its
   timetable schedules it at whose scheduled departure is still to come when the
   cancellation arrives, so an earlier prediction of such a stop no longer counts as a
   departure. A stop whose scheduled departure has passed keeps its departure, so a
   late-running trip cancelled between a stop's scheduled and its delayed departure keeps
   that stop's last prediction: the cancellation carries no delay, and ingest goes by the
   timetable alone. A trip with no stop still to come, or with no stops in the timetable,
   is stored as one trip-level row: it counts as not run, and is not a stop. Days ingested
   before method `2026.11.6` keep the earlier shape, the one trip-level row, unless they
   are ingested again from the archive. Each stop's cancellation takes the route the
   feed's cancellation names, or the timetable's when it names none, so it replaces a
   prediction filed under the same route. A feed that names one route on its predictions
   and a different one on the cancellation files the two apart, and the predictions keep
   counting.
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
may differ from today's for the same period. The analyze run that rebuilds an agency's
history records its headline on-time rate and average delay before and after, over the
days it rewrote, on the admin board's run timeline (`pipeline/rebuild_headline.py`).
Figures from a live scan of the
observations, such as a time-band view or the delay record, name the version but no
build day. The stamp also names each timetable in force over the period: its version
name, the day it was fetched, and the first eight hex digits of the content hash the
timetable archive maps the name to (`static_version_map`), read from its Postgres mirror
`static_version_contents` rather than from ClickHouse. A name the archive does not map
shows no hash.

| Version | What changed | Tables |
|---|---|---|
| `2026.11.0` | Baseline. The latest reading per trip, stop and service day counts, and readings more than 120 minutes off are dropped. Stop, dwell/run and headway rows join the timetable each reading's feed version archived, and each day's long gaps are measured against that timetable's own headway. | All |
| `2026.11.1` | A departure scheduled at 24:00 or later counts under its service day's hour 24, 25, … and in the time band of its clock time (24:30 is late night), where the hourly and per-stop banded tables had left it out. | `agg_hour_daily`, `agg_route_hour`, `agg_route_hour_dow`, `agg_stop_daily`, `agg_route_stop_daily`, `route_delay_counts` |
| `2026.11.2` | Each day's feed-health row also counts its observations by ingest source: from the collector's archive, or from the live path. The stamp files each day under the source most of its observations came from. No delay or on-time figure changes. | `agg_feed_health` |
| `2026.11.3` | Each reading is filed under the timetable in force on its service day: the newest loaded timetable whose feed dates, or else calendar, cover that day. Until now a reading took whichever timetable was loaded when it arrived, so a timetable published ahead of its first day was applied to the rest of the day before. Such readings now take the timetable in force for their scheduled time, day type and stop, and for their route where the feed named none. A day no known timetable covers keeps the timetable its readings joined. | `agg_daily_trend`, `agg_route_daily`, `agg_route_daily_dist`, `agg_hour_daily`, `agg_stop_daily`, `agg_route_stop_daily`, `agg_route_stats`, `agg_route_hour`, `agg_route_hour_dow`, `agg_stop_routes`, `agg_route_daily_dwell_run`, `agg_route_headway_daily`, `agg_schedule_revision_daily`, `route_delay_counts`, `stop_route_days` |
| `2026.11.4` | The feed's last report on a stop decides whether it departed. A trip reported CANCELED, or a stop reported SKIPPED, after a prediction of it leaves no departure, where the prediction had counted as one; a prediction reported after either restores it. A trip is not run when its last status is CANCELED, or when every stop it reports with a delay or a status was last so reported SKIPPED; a stop reported only without either counts neither way, and a trip that skipped only some stops ran, where any skipped stop had counted it as not run. A report with neither a delay nor a CANCELED or SKIPPED status leaves the report before it standing, in the delay figures and the service-delivered rate alike. A trip cancelled without listing any stop is kept and counts as not run, where it had been lost. | `agg_daily_trend`, `agg_route_daily`, `agg_route_daily_dist`, `agg_hour_daily`, `agg_stop_daily`, `agg_route_stop_daily`, `agg_route_stats`, `agg_route_hour`, `agg_route_hour_dow`, `agg_route_daily_dwell_run`, `agg_route_headway_daily`, `agg_service_delivered_daily`, `route_delay_counts` |
| `2026.11.5` | Each agency's monthly feed-quality scorecard: RT and stop-event completeness, message age at capture, the clamp share and RT field coverage (see "Feed-quality scorecard" below). No delay or on-time figure changes. | `feed_quality_monthly` |
| `2026.11.6` | A trip cancelled without listing any stop is stored as a cancellation of each stop its timetable schedules it at whose scheduled departure is still to come when the cancellation arrives, so an earlier prediction of such a stop no longer counts as a departure, as when the cancellation lists its stops; a stop whose scheduled departure has passed keeps its departure, even when the trip was running late. A trip with no stop still to come, or no stops in the timetable, keeps the one trip-level row. A trip whose last status is CANCELED counts as not run, so one cancelled partway, or after its last stop, does too. This changes how such a cancellation is ingested, not how any delay or service-delivered table is built, and days ingested earlier keep the one trip-level row unless ingested again. The feed-quality scorecard counts those rows as reported stop events but not as stop_time_updates; it tells them apart by their shape alone, so a stop update the feed sends for a cancelled trip with neither a delay nor a stop status is left out of its field coverage and update count too. | `feed_quality_monthly` |
| `2026.11.7` | Each day's trips not run are counted per route: under the route the trip's latest report naming one names, or under no route where none does, so an agency's day is the sum of its routes. This gives each route its own service-delivered rate (see "Service delivered per route" below). No agency figure changes. | `agg_service_delivered_daily` |
| `2026.11.8` | The feed-quality scorecard's message age counts archived polls beside live ones. Archive ingest stamps an archived poll with its own FeedHeader timestamp, so the poll's capture instant is read from its file name: the UTC day and time of day the collector names it by as its fetch begins. A poll whose name gives no instant is left out of the ages and counted beside them. A header frozen for hours reads as hours of age on every poll that received it. Until now only live polls counted, so a month of archived polls had no message age. No delay or on-time figure changes. | `feed_quality_monthly` |

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
  up to 5. Any other window is estimated from the delay histogram, within one minute,
  except a late tolerance of 30 minutes or more or an early tolerance over 5 minutes:
  those bounds fall in the histogram's open end buckets, so the window is scanned live
  from the raw observations. A time-band filter is scanned live too.
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

## Feed-quality scorecard

Each analyze run keeps `feed_quality_monthly`, one row per agency and calendar month of
service date (`pipeline/feed_quality.py`). A month counts its service dates from the 1st
through its last day, or, for the month in progress, through the last closed service
day, and never past the newest day any agency's aggregates reach, where the agencies
board's coverage stops too: a later day is a lag every feed shares, not trips one feed
missed. `through_date` names where it stops. A month is rebuilt when a day of it changed
or when its window has grown since it was built, so a feed that stops reporting still
has its planned days counted against it once other feeds' days move past it. An agency
analyzed before the others in the same run counts the days they reached by its turn,
and its next run catches up.

| Figure | Definition |
|---|---|
| Trips reported (RT completeness) | Trips the feed reported a plausible departure for, of the trips the timetable planned, over the month's planned days. The agencies board's coverage, by the same rule and SQL (`pipeline/reports/comparability.py`, `pipeline/reports/service_delivered.py`), so a month reads what the board reads over the same days. |
| Stop events | Stop events the feed reported, counted once per trip, stop and service date however often reported, of the stop times the planned trips call at. A stop event is reported when any poll of it carries a plausible departure or arrival delay, or reports its trip CANCELED or its stop SKIPPED. This is reporting coverage, not the service-delivered rate: a cancellation is something the feed said about the stop, so it counts here, where the departure figures let the feed's last report decide whether the stop departed. Only the planned days whose stop events are known count (`stop_plan_days`): the day's capture saves them beside its planned trips, and a day saved before it did keeps them unknown. |
| Message age | Per poll, the instant it was captured minus its FeedHeader timestamp: the median and 95th percentile, interpolated linearly between the closest ranks (ClickHouse's `quantilesExactInclusive`, the rule of numpy's default), with the number of polls measured. A live poll was captured at its stored capture time. An archived poll's stored time is its own header (or, where the header is far from its name, its name read as JST), so its capture instant is read from its file name instead: `YYYYMMDD/TripUpdate_HHMMSS.pb`, the UTC day and time of day the collector (`oracle_cloud/v3/bin/rt-poller.sh`) names it by as its fetch begins. A name of any other shape, or one naming no real day and time, gives no instant: the poll is left out of the ages and counted (`message_age_undated_polls`), its zone never guessed. A header frozen for hours reads as hours of age on every poll that received it. A message generated during a slow fetch is newer than the name and reads a small negative age, which stands as measured. |
| Implausible readings | The board's clamp share: readings beyond the plausibility clamp, of the readings that carried a delay (`agg_feed_health`). |
| Field coverage | For each of `stop_id`, `arr_delay`, the trip's and the stop's `schedule_relationship`, the share of stored stop_time_updates carrying it: the unit the RT field-coverage probe measures one poll in. The rows ingest writes from the timetable for a trip cancelled without listing any stop are no stop_time_update the feed sent, so they count here neither way, while each still reports its stop event. They are told apart by their shape alone (trip CANCELED, no delay, no stop status), so a stop update the feed sends for a cancelled trip in that same shape is left out with them. |
| VehiclePositions | Not recorded: nothing the app reads says which feeds publish them. |

A figure the feed or the app cannot supply is empty beside a reason, never 0. Trips and
stop events need trip IDs matched to the timetable (`coverage_unknown` otherwise, the
gate's reason) and a plan (`no_plan`); stop events also need a known stop plan
(`no_stop_plan`). Message age needs a poll whose capture instant is known, live or
under an archive name that gives one (`archive_stamped`), with a header (`no_header`).
Field coverage needs an ingest strategy that stores the fields (`feed_cannot_report`),
and every figure needs an observation (`no_data`). The admin agency drawer shows the
newest month and the five before it.

## Service delivered per route

A route's service-delivered rate is the agency's ratio on the route's own trips: its
planned trips, less the trips the feed reported as not run under it, of its planned
trips (`pipeline/reports/service_delivered.py`). Not run means what it means for the
agency: the trip's latest status is CANCELED, or every stop it reports with a
plausible delay or a status was last reported SKIPPED.

- **Trips not run.** `agg_service_delivered_daily` counts them per service date and
  route (`pipeline/agg_sql.py`). Each trip-day counts once, under the route its latest
  report naming a route names, or under no route (`''`) where none does, so an
  agency's day is the sum of its routes. It is rebuilt per changed date, as the other
  dated tables are.
- **Planned trips.** Each day's capture (`pipeline/planned_trips.py`) saves the plan
  split by route beside its count, from the same timetable, so a day's routes sum to
  its planned trips. A route is keyed by the code its static `route_id` maps to, and
  a trip naming no route by `''`. A day the capture has not saved is split by the
  loaded timetable. A day saved before the split was saved keeps it unknown: its
  timetable may be gone.
- **Which days count.** The days the agency's rate counts. A route's figure is read
  only when every one of those days is split, so it covers the same days as the
  agency's, and the routes' planned trips and trips not run then sum to the agency's.

A performance standard on `service_delivered_pct` is judged on this rate over the
request's dates (`pipeline/reports/performance_standard.py`), and a
`vehicle_km_delivered_pct` standard on the same rate where the timetable has route
shapes, the proxy `pipeline/reports/supply.py` applies. A route is judged only where
the agency's own rate exists and every counted day is split, at the on-time
standard's floor: at least `RANKING_MIN_SAMPLES` (100) planned trips and as many
observations. A route the feed barely reports has no trip reported not run, and a
route whose feed reports zero delay only echoes the timetable; either would read 100%.
Each row that is not judged says why.

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
