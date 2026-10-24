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
