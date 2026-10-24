# ML workbench (VPS)

The VPS runs a replica of production history and the delay-forecast work on it.
The replica is rebuilt from R2 and is neither the dev stores nor production:
writes there are expected, and losing it costs only a re-sync.

R2 holds the only complete history, so the replica is where the forecasting work
gets one queryable copy of all of it. The baselines and the backtest read their
cells from it. The first sync replays every archive in R2, which takes hours.
After that, each nightly run adds only the archives that have become final.

## What runs

| Timer (Asia/Tokyo) | Job | Output |
|---|---|---|
| daily 10:30 | `python -m ml.cli sync` | R2 archives replayed into the replica in timeline order; done-set in `/var/lib/transit-ml/sync-state.json` |
| Sundays 04:00 | `backtest` → `train-eval` → `report` → `train`, chained | `/var/lib/transit-ml/backtests/latest.json`, `models.json`, `/var/lib/transit-ml/reports/latest/index.html`; `train` publishes a new model only if it clears the adoption bar (see Models) |
| daily 11:00 | `python -m ml.cli predict` | `/var/lib/transit-ml/forecast/forecast.parquet`, uploaded to R2 as the latest published forecast |

`train` runs last in the Sunday chain so a publish failure can never suppress the
report. Each job reports to healthchecks.io through `deploy/vps/run-with-ping.sh`
(`HC_PING_URL_SYNC`, `HC_PING_URL_BACKTEST`, `HC_PING_URL_PREDICT`).

The sync runs `ingest` and `load_static` only. Nothing here reads `agg_*` yet, so it does not run `analyze_all`.

## Setup

1. Copy `deploy/vps/env.example` to `/etc/transit-ml/env` (root, mode 600) and fill it. Two separate R2 tokens are required: a read-only one scoped to `transit-archives` (`OBJECT_STORE_*`, what `sync` reads from) and a read-write one scoped to `transit-ml` (`ML_STORE_*`, what `train`/`predict` publish to). `train` and `predict` swap `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` to the `ML_STORE_*` pair only for their own short-lived process, so the read-only pair is never exposed to a process that writes.
2. `bash deploy/vps/bootstrap.sh` from `/root/transit-app`. It is safe to re-run.

## Operating it

- View the report: `ssh -L 8000:127.0.0.1:8000 root@<vps> 'cd /var/lib/transit-ml/reports/latest && python3 -m http.server 8000 --bind 127.0.0.1'`, then open http://localhost:8000.
- A failed sync leaves that agency's later days for the next run. The done-set holds only archives whose rows are all in, so a realtime archive stays out of it until the JST day after its UTC day has ended.
- Sync runs ingest with `--strict`, so an ingest that skipped a file fails its action: the archive stays out of the done-set and the next run retries the skipped files. A file that can never ingest, such as a corrupt member, keeps failing there; once its log line is read, add the archive's key to the done-set by hand to move that agency past it.
- A `static_join` agency's realtime days that come before its first static archive in R2 cannot ingest: `ingest` refuses an agency with no timetable loaded (exit 78), so that agency's timeline stops at its first realtime action on every run, and its later static archive never loads. Add those early realtime keys to the done-set by hand to move the agency on to its first timetable. Those days are then left out of the replica, since no timetable covers them.
- A static archive that cannot load (an error page stored as a zip, or a GTFS file the loader rejects) stops that agency's timeline on every run. Once the failure is read, give up on that timetable by running sync once by hand with its bucket key, as root from `/root/transit-app`, with the unit's environment loaded and the same `--state`/`--work` paths `deploy/vps/systemd/transit-ml-sync.service` passes, so the skip lands in the done-set the timer reads: `set -a; . /etc/transit-ml/env; set +a; /root/transit-app/.venv/bin/python -m ml.cli sync --state /var/lib/transit-ml/sync-state.json --work /var/lib/transit-ml/work --skip-static <key>`. Run it while the timer's own sync is not active, since each run rewrites the whole done-set. The archive is marked done without being fetched, the skip is printed, and the agency's later days then replay under the previous timetable. Nothing is skipped without that flag, so the choice is always an operator's.
- To replay from scratch: stop the timers, remove the stack (`docker compose -f deploy/vps/compose.yml --env-file /etc/transit-ml/env down`), then remove its volumes by name (`docker volume rm transit-ml_ml_pg transit-ml_ml_ch`; the shared dev-DB hook blocks `down --volumes` for every compose project), delete the done-set, and run the bootstrap again.

## How the data grows

- The replica keeps every day it syncs, and nothing expires it. ClickHouse stores `updates` compressed at a few bytes per row, so a year of every agency comes to the order of ten gigabytes, well inside the VPS disk.
- Postgres grows with each timetable version the collector archived.
- Nothing else accumulates. Downloads are deleted after each sync action, ClickHouse's own logs either expire after a few days or are turned off, and each report replaces the last.

## Trimming it

R2 keeps everything, so old history can leave the replica whenever the disk needs it. `updates` is partitioned by UTC month of `captured_at`, so removing one is a metadata operation rather than a rewrite: `ALTER TABLE updates DROP PARTITION 202603` through `clickhouse-client` in the `ml-ch` service. The done-set still lists those archives, so the nightly sync leaves them out. Bringing a removed month back means a replay from scratch, because the replay has to load each timetable version before the days it governs.

## Baselines

B0 is a seasonal-naive baseline: the same route×weekday×hour, pooled by runs, over the 28 days before the forecast day. It is not the app's expected delay (`agg_route_hour_dow`), which pools the whole history by stop observation, takes each stop's own scheduled hour and drops rows without a service type, so "skill vs B0" does not measure improvement over what the app shows. B1 is the latest such cell, and B2 is the route's 28-day mean. The report scores each on T+1..T+7 from data through T−1. It measures skill against B0 only where both predict, and shows coverage beside every error.

## Models

The weekly job also backtests LightGBM forecasters of each trip run's mean delay: one model for the mean and one each for the 10th, 50th and 90th percentiles. They are trained on runs from every agency (`python -m ml.cli train-eval`, which needs the optional `ml` Poetry group the bootstrap installs).

- **Features** come from runs on or before the day before the forecast is made: the route×weekday×hour slot's mean (B0), count, spread and latest day (B1); the route's means over the window, the last week, the last three days and the day before; the trip's own mean, typical stop count and scheduled span; and how many days the agency was observed in the window. The run's own timetable facts are added: hour, weekday, days ahead, and how often its service runs at weekends, which marks holiday timetables.
- **Training** makes every past run a target once, forecast from an origin 1–7 days earlier with the lead drawn at random, so the lead never stands in for the weekday. Older targets weigh less.
- **Intervals**: the quantile models are trained without the last week before the cutoff, and that week calibrates the 10th–90th percentile interval to hold 80% of runs (conformalized quantile regression).
- **Evaluation** chooses 28 origins from every agency's pooled dates, the same way the baselines do from one agency's, retraining once per week of origins on data before it. Point error is compared with B0 on the cells both predict. Intervals are scored per run, against B0's own slot 10th–90th percentile.
- **Short history**: an agency with fewer than 56 days of history before a cutoff keeps B0, because a model trained mostly on other agencies forecasts it worse. It moves to the model on its own once it has the history, and the report names the agencies kept on B0.
- **Adoption** requires at least 5% better error than B0 overall, no agency worse than B0, and an interval that holds 75–85% of runs. The report states the verdict and the reasons it fails. Weekly retraining means this is re-decided every run: a week that clears the bar publishes a new model version; a week that doesn't keeps serving whichever version is already published (or none, if none ever has).

## Publishing and serving the forecast

An adopted model is written to `/var/lib/transit-ml/model` and uploaded to R2 under
`models/<version>/` plus a `models/latest.json` pointer, where `<version>` is the
training cutoff date. The daily `predict` job loads whichever version
`models/latest.json` names, forecasts each on-model agency's routes for the next
`HORIZON_DAYS` days, and uploads the result as the latest published forecast.
`predict` needs the target days' rows to already exist in `runs` to build their
features, which ClickHouse's observed-only history never has for the future. It
copies each future day's runs from a recent day of the same day type (weekday,
Saturday, or Sunday-and-holiday), in `ml.features.template_future_runs`:

- **Day type** follows the app's rule (`pipeline/day_types.py`): the agency's
  feed calendar in the replica's Postgres where it covers the date, the weekday
  rule with Japan's public holidays otherwise. A replica whose migrations predate
  `static_calendar`, or whose timetables were loaded before it existed (sync
  never reloads a timetable already in its done-set), has no calendar to read,
  so its agencies use the weekday rule alone.
- **The template** is the most recent complete day of that type in the agency's
  last 14 days before the origin, never the origin's own partial day and never
  another agency's day. A target day with no such day is not forecast; it never
  falls back to the same weekday.
- **A partial day** has fewer than half the route×hour slots of the median of the
  agency's other days of its type in the last 28 days, and is passed over for
  the next most recent day of its type.
- **A stopped feed** is not forecast. An agency publishes only while its newest
  complete day is at most 2 days before the origin. The 10:30 sync has ingested
  every closed JST day, so a current feed's newest day is the day before; the
  second day absorbs one sync or archive upload that has not landed by 11:00.

The backtest and training never template. They score and learn from the runs
that actually happened on each target day. The backtest therefore measures the
model on the true timetable, while the live forecast has only a template of it,
so a day the template gets wrong costs the live forecast skill that the backtest
never sees.

Outside the VPS, production's `/internal/cron/ingest` sweep polls R2 for a newer
published forecast and upserts it into Postgres's `agg_route_forecast_ml`
(`pipeline/ml_forecast_ingest.py`), keyed by `(agency_id, route_code, service_date,
hour)` so a forecast is replaced in place rather than accumulating duplicates, and
pruned once its service date is old enough. The `ml_forecast` flag
(`pipeline/flags.py`, default off) gates whether `/forecast/heatmap` and
`/forecast/overview` overlay this table's rows (`source: "ml"`) over the seasonal
baseline (`source: "baseline"`); with the flag off, the ingest still runs and the
table still fills, but the API never reads it.
