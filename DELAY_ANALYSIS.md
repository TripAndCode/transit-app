# How delay analysis works

An overview of what this app actually does with transit delays. For the architecture
details see `README.md`; for AI-assist conventions see `CLAUDE.md`.

## The raw data

We poll each agency's real-time feed (GTFS-RT) on a schedule and save every reading to
the `updates` table. One row means: **"this trip, at this stop, was N seconds late."**

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
   - `agg_route_dow` — route × day-of-week → average delay
   - `agg_route_daily` — route × day → average delay
   - `agg_route_stats` — route overall (avg, p50, p90, on-time %)
   - …and per-stop / per-hour / feed-health variants

## Serving

Every read endpoint just **reads** an `agg_*` table — it never scans the raw `updates`
table live. That is why the app is fast regardless of how much raw data has piled up.
After a fresh ingest, `make analyze` (or `make analyze-all`) rebuilds these tables.

## What "forecasting" would mean here

It is **not** prediction with a trained model. It is **looking up an average we already
computed**:

> "Route X, on weekdays, in the 5 p.m. hour → historically +8 min on average"
> is one row read from `agg_route_hour`.

This is a *seasonal-naive baseline* (a.k.a. climatology): the future is assumed to
resemble the historical pattern for that route at that time. It is honest to call it
**"expected" / "typical" delay**, not a prediction — it cannot see disruptions, trend
drift, weather, or special events. The only fiddly parts are mapping a calendar date to
its service type (weekday / Saturday / Sunday-holiday) and bucketing a clock time into an
hour; the core is "look up one average."
