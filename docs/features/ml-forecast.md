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
| Sundays 04:00 | `backtest` → `train-eval` → `report` → `train` → `publish-eval`, chained | `/var/lib/transit-ml/backtests/latest.json`, `models.json`, `/var/lib/transit-ml/reports/latest/index.html`; `train` publishes a new model only if it clears the adoption bar (see Models); `publish-eval` uploads `evaluations/<run_date>/summary.json` to the `transit-ml` bucket whatever the verdict |
| daily 11:00 | `python -m ml.cli predict` | `/var/lib/transit-ml/forecast/forecast.parquet`, uploaded to R2 as the latest published forecast |

`train` runs after the report so a model-publish failure can never suppress the
report, and `publish-eval` runs last so a failed summary upload can never stop an
adopted model going out (`train` exits 0 on a rejected week, so the summary is
still published). Each job reports to healthchecks.io through
`deploy/vps/run-with-ping.sh` (`HC_PING_URL_SYNC`, `HC_PING_URL_BACKTEST`,
`HC_PING_URL_PREDICT`).

The evaluation summary is the web app's copy of that week's verdict: `adopted`,
the `failures` as codes with their measured values (worded by the app, not the
workbench), the judged headline skill and coverage with the thresholds, overall
and per-agency figures, every interval method, the origin-skill spread, the
`params` and `point_metric` the run scored with, and the serving model's
`meta.json` (`null` before any model is published). Each agency carries a
`model_status`: `model` (on the model at every cutoff), `b0` (on B0 at every
cutoff for too little history, so no skill: its 0% is how it was scored, not how
the model did) or `partial` (it crossed the history threshold mid-backtest, so
its skill blends B0 weeks with model weeks and is shown, since the verdict
judges it). `schema` versions the document.

The sync runs `ingest` and `load_static` only. Nothing here reads `agg_*` yet, so it does not run `analyze_all`.

## Setup

1. Copy `deploy/vps/env.example` to `/etc/transit-ml/env` (root, mode 600) and fill it. Two separate R2 tokens are required: a read-only one scoped to `transit-archives` (`OBJECT_STORE_*`, what `sync` reads from) and a read-write one scoped to `transit-ml` (`ML_STORE_*`, what `train`/`predict`/`publish-eval` publish to). Each of those jobs swaps `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` to the `ML_STORE_*` pair only for its own short-lived process, so the read-only pair is never exposed to a process that writes.
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

The weekly job also backtests LightGBM forecasters of each trip run's delay: one model each for the 10th, 50th and 90th percentiles, plus a mean model only when a variant or the published point reads it. They are trained on runs from every agency (`python -m ml.cli train-eval`, which needs the optional `ml` Poetry group the bootstrap installs). The point forecast, the one scored against B0 and the one a published cell shows, is the model `ModelParams.point` names: the median model's by default (`DEFAULT_POINT`), since a cell is scored by its absolute error and the median does not follow one extreme run in a slot's history the way the squared-error mean does. A published model's `meta.json` records the point it was adopted with, and `predict` uses it; a meta that records none belongs to a model adopted on the mean, which then serves the mean model's forecast (see Publishing and serving the forecast). The weekly summary's `point_metric` says which point that week scored.

- **Features** come from runs on or before the day before the forecast is made: the route×weekday×hour slot's mean (B0), count, spread and latest day (B1); the route's means over the window, the last week, the last three days and the day before; the trip's own mean, typical stop count and scheduled span; and how many days the agency was observed in the window. The run's own timetable facts are added: hour, weekday, days ahead, and how often its service runs at weekends, which marks holiday timetables.
- **Training** makes every past run a target once, forecast from an origin 1–7 days earlier with the lead drawn at random, so the lead never stands in for the weekday. Older targets weigh less.
- **Intervals**: the quantile models are trained without the last week before the cutoff, and that week calibrates the 10th–90th percentile interval to hold 80% of runs (conformalized quantile regression).
- **Evaluation** chooses 28 origins from every agency's pooled dates, the same way the baselines do from one agency's, retraining once per week of origins on data before it. Point error is compared with B0 on the cells both predict. Intervals are scored per run, against B0's own slot 10th–90th percentile. The backtest also scores the interval `predict` publishes, one per route×date×hour cell made of its runs' mean bounds, against each run in the cell (`LGBM slot`). Its bounds are one pair per cell rather than per run, so its coverage can differ from the per-run interval's wherever a cell's trips differ.
- **Short history**: an agency with fewer than 56 days of history before a cutoff keeps B0, because a model trained mostly on other agencies forecasts it worse. It moves to the model on its own once it has the history, and the report names the agencies kept on B0.
- **Adoption** requires at least 5% better error than B0 overall, no agency worse than B0, and an interval that holds 75–85% of runs. The report states the verdict and the reasons it fails. `train` fits the published model with exactly the parameters the judged backtest recorded, so what is published is what was judged. Weekly retraining means this is re-decided every run: a week that clears the bar publishes a new model version; a week that doesn't keeps serving whichever version is already published (or none, if none ever has).

### Comparing variants

`ml.compare.FITS` names the fits a backtest can compare:

- `base`, the default parameters;
- `clip`, which winsorizes each agency's window history at its own 1st and 99th percentiles before the delay features are built, and clips the mean model's training target to the same bounds;
- `huber`, which trains the mean model with a Huber loss;
- `clip-huber`, both.

Each fit is scored with the median model's forecast as the point (`--point q50`, named `<fit>-q50`), the mean model's (`--point mean`, named `<fit>`), or both from one fit. B0 is always the slot's raw mean, so every variant is scored against the same baseline. Each variant is one full `train-eval` with its memory peak, so run them one process at a time, outside the timers' windows, from one snapshot of the replica so that every variant scores the same origins:

```
cd /root/transit-app && set -a && . /etc/transit-ml/env && set +a
nice .venv/bin/python -m ml.cli snapshot-runs --out <dir>/runs.parquet
nice .venv/bin/python -m ml.cli variant-eval --runs <dir>/runs.parquet --fit base --point mean --point q50 --out-dir <dir>/variants
nice .venv/bin/python -m ml.cli variant-eval --runs <dir>/runs.parquet --fit clip --point mean --point q50 --out-dir <dir>/variants
nice .venv/bin/python -m ml.cli variant-eval --runs <dir>/runs.parquet --fit huber --point mean --out-dir <dir>/variants
nice .venv/bin/python -m ml.cli compare --dir <dir>/variants
```

`compare` refuses variants scored on different origins. It writes `comparison.json` and `comparison.md` beside the variants: each variant's skill against B0 overall, per agency and by days ahead, RMSE, both intervals' coverage, its adoption verdict, and on how many origins it beats the `--baseline` variant: by default `base-q50`, what is published. `variant-eval` scores the published point unless `--point` names others.

## Publishing and serving the forecast

An adopted model is uploaded to R2 under `models/<version>/` plus a
`models/latest.json` pointer, where `<version>` is the training cutoff date, and
only then replaces `/var/lib/transit-ml/model`. `train` stages it in a sibling
directory first, so a failed upload leaves the last published model in place and
every forecast names a model R2 holds. The daily `predict` job loads the model
`train` last wrote to `/var/lib/transit-ml/model` (it does not read
`models/latest.json`),
forecasts each on-model agency's routes for the next
`HORIZON_DAYS` days, and uploads the result as the latest published forecast.
Each cell's `mean_min` is the model's point forecast and `q10`/`q90` its interval,
each the mean over the cell's trips. The model's `meta.json` records the point it
was adopted with and the history clip its features were built with, and `predict`
uses both; a meta without them belongs to a model that shows its mean, unclipped.
The mean model is trained, run and published only when something reads it: a
model whose point is the median has no `mean.txt`, and a backtest trains it only
when asked to score the mean (`variant-eval --point mean`).
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
- **A guessed day is never a template.** The replica keeps only each feed's
  current calendar, so a day before it, like every day of an agency with no
  calendar, is typed by the weekday rule alone. Such a day is skipped when it is
  a public holiday or in the 12/29–1/3 year-end break, because feeds run some
  holidays on their weekday timetable and the year-end break on a holiday one.
  It still counts as the feed's latest day for the stopped-feed rule below.
- **A partial day** is never a template; the next most recent day of its type
  is used instead. A day is partial when, against the median of the previous 3
  days of its type, it has fewer than half the route×hour slots, or its first run
  starts 4 or more hours after theirs or its last run ends 4 or more hours before
  theirs. A stopped feed or a
  missing archive leaves such a fragment. The reference reaches 5 weeks back, so
  even a Saturday at the far end of the 2-week lookback has 3 earlier Saturdays to
  be judged against; a day with no earlier day of its type in that window is not
  partial. Judging against only the last 3 days lets a
  permanent timetable change become the template once two days of it have run:
  its first two days are measured against the old timetable and skipped.
- **A stopped feed** is not forecast. An agency publishes only while its newest
  day with any observed run is at most 2 days before the origin; a fragment
  counts, so no timetable change can silence an agency. The 10:30 sync has
  ingested every closed JST day, so a current feed's newest day is the day
  before; the second day absorbs one sync or archive upload that has not landed
  by 11:00.

The backtest and training never template. They score and learn from the runs
that actually happened on each target day. The backtest therefore measures the
model on the true timetable, while the live forecast has only a template of it,
so a day the template gets wrong costs the live forecast skill that the backtest
never sees.

Outside the VPS, production's `/internal/cron/ingest` sweep polls R2 for a newer
published forecast and upserts it into Postgres's `agg_route_forecast_ml`
(`pipeline/ml_forecast_ingest.py`), keyed by `(agency_id, route_code, service_date,
hour)` so a forecast is replaced in place rather than accumulating duplicates, and
pruned once its service date is old enough. An upsert leaves alone the slots a
publish does not forecast, so a route, hour or agency that an earlier publish
covered and the latest one dropped keeps the earlier model's row until the prune.
The API therefore reads only the latest publish (the rows with the newest
`generated_at`, which one publish shares), on top of its freshness and horizon
checks (`api/routers/reports.py`, `_ML_FRESH_ROWS`). "Latest" is across every
agency, so an agency the newest publish leaves out (a stopped feed) drops at
once. It also means a partial publish, such as a manual
`ml.cli predict --agency <id>`, once ingested, hides every other agency's
forecast until the next full sweep: publish full sweeps only. The `ml_forecast` flag
(`pipeline/flags.py`, default off) gates whether `/forecast/heatmap` and
`/forecast/overview` overlay this table's rows (`source: "ml"`) over the seasonal
baseline (`source: "baseline"`); with the flag off, the ingest still runs and the
table still fills, but the API never reads it. The same step upserts every weekly
evaluation summary under `evaluations/` into `ml_evaluations`
(`pipeline/ml_evaluation_ingest.py`; one row per run date, the document as JSONB),
skipping by name any document that is not JSON, uses a schema other than 1, is
missing a field, names a run date other than its key's, or holds a non-finite
number; a week whose document is unchanged is left as it is. A summary that
cannot be fetched at all is a read failure, not a skipped document. Each of the two steps is guarded on its
own, so one failing never skips the other.

Locally, nothing runs that sweep. `make ingest-ml` pulls the latest published
forecast and every evaluation summary into the database `.env`'s `DATABASE_URL` names, reading `ML_STORE_*`
from the same file (a read-only token is enough). Unlike the sweep it exits
non-zero on a missing variable, an unreadable publish or a failed upsert, and
says "no forecast has been published yet" (and, for the summaries, "no evaluation
summary has been published yet") rather than reporting zero rows; a summary it
skips is named in a warning. The
heatmap shows the ingested rows once the `ml_forecast` flag is on (admin → Flags),
and marks them as the ML model's: each forecast cell shows its `q10`–`q90` range
as the span about 8 runs in 10 fall in, and a cell the latest publish does not
forecast shows its past average with a mark.
