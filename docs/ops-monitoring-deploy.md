# Deploying and operating the VPS monitoring hub

The monitoring hub is the combined operations-status page/CLI/JSON endpoint
(`scripts/ops_status_server.py`, `scripts/ops_status_page.py`) plus
anomaly-only alerting (`scripts/ops_alerts.py`) on top of it. Both already
ship as systemd units in `deploy/systemd/`; this doc is the install/rollback
walkthrough and the operational facts that don't belong duplicated across
every unit file's own header comment.

## 1. What runs, and as what

| Unit | Type | Trigger | Purpose |
|---|---|---|---|
| `ops-status.service` | `simple` (long-running) | `systemctl enable --now` | Serves the authenticated HTML/text/JSON status page on `127.0.0.1:8642`. |
| `ops-alerts.service` + `ops-alerts.timer` | `oneshot` | Timer, every 5 minutes | One poll: compares the current combined status document against persisted history and reports only genuine transitions (see `scripts/ops_alerts.py`'s own docstring). |

Both run as `User=root` under `WorkingDirectory=/root/transit-app`, matching
every other VPS-side automation this repo already runs that way (see
`.claude/README.md`'s "VPS operations" section — the persistent clone itself
is provisioned and owned as `root`). Splitting these two units onto a
separate, narrower system user would need re-provisioning the checkout's own
ownership without affecting every other root-run VPS unit that reads/writes
the same tree — out of scope here; least-privilege for this feature instead
comes from bounding *credentials*, not the OS user (see section 2).

## 2. Least-privilege credentials

Only one genuinely new secret exists for this feature: `OPS_STATUS_TOKEN`,
the HTTP Basic password gating the status page (see
`deploy/systemd/ops-status.env.example`). It is VPS-local, unrelated to any
other credential in this repo, and grants nothing beyond read access to the
already-read-only status page — generate it with `openssl rand -hex 32` and
never commit it.

Nothing else on the VPS side needed a new or broadened credential:

- **GitHub** (`scripts/collect_github_status.py`): shells out to the `gh`
  CLI, reusing whatever `gh auth` session already exists on the
  VPS — no separate token, and no new scope requested.
- **Oracle** (`scripts/collect_oracle_status.py`): reads a heartbeat
  relayed through GitHub Actions run logs. The VPS never holds an Oracle
  credential of any kind, and specifically never the Oracle SSH private key
  — a compromised VPS gains nothing toward Oracle.
- **R2** (`scripts/collect_r2_status.py`): same relay pattern as Oracle —
  storage metrics are aggregated on Oracle (where the R2 credentials
  already live) and only the aggregated numbers are relayed through GitHub.
  The VPS never holds an R2/AWS credential.

The optional `OPS_ALERT_PING_URL` (section 5) is the only other
secret-shaped value this feature can introduce, and only if an operator
chooses to configure real notification delivery.

## 3. Install

```bash
# 1. Status page token (required — the page fails closed without it):
sudo install -m 600 -o root -g root /dev/null /etc/transit/ops-status.env
echo "OPS_STATUS_TOKEN=$(openssl rand -hex 32)" | sudo tee /etc/transit/ops-status.env

# 2. Alerting overrides (optional — see ops-alerts.env.example):
sudo install -m 600 -o root -g root /dev/null /etc/transit/ops-alerts.env

# 3. Install the units:
sudo cp deploy/systemd/ops-status.service deploy/systemd/ops-alerts.service \
     deploy/systemd/ops-alerts.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ops-status.service ops-alerts.timer

# 4. Log rotation:
sudo cp deploy/logrotate/ops-monitoring /etc/logrotate.d/ops-monitoring
sudo logrotate -d /etc/logrotate.d/ops-monitoring   # dry run, confirm no errors
```

Each unit file's own header comment covers the file-specific details
(paths, exact env var names); this doc only sequences them.

## 4. Restart limits

`ops-status.service` sets `StartLimitIntervalSec=300`/`StartLimitBurst=6`
alongside its existing `Restart=on-failure`/`RestartSec=5`: six crashes
within five minutes stops systemd from retrying further (`systemctl status`
then shows a plain `failed` instead of an endless restart cycle chewing CPU
on, e.g., a bad `EnvironmentFile` or the port already in use elsewhere).

`ops-alerts.service` deliberately has no such limit — see the comment in
that file. It is a `Type=oneshot` unit invoked fresh by the timer every 5
minutes, and exit code 1 is its *expected*, correct outcome for as long as a
real anomaly is ongoing. A start limit here would eventually stop the timer
from being able to start new polls at all mid-incident, silencing the one
thing meant to keep firing throughout it — worse than the crash-loop such a
limit would otherwise guard against.

## 5. Notification endpoint

`scripts/ops_alerts.py` can forward every poll's outcome to an external
endpoint via `OPS_ALERT_PING_URL`, reusing the same healthchecks.io-style
convention `oracle_cloud/v3/bin/alert-lib.sh` already uses on the Oracle
collector side: a bare ping to the configured URL means "nothing is bad right
now", a ping to `<url>/fail` (carrying the rendered alert text, or a summary of
what is still bad, as the POST body) means "something is bad". Both follow the
current health, so a poll inside the dedup window during an outage still
sends `/fail`. To set it up:

1. Create a check at a healthchecks.io-compatible provider (or point the
   variable at any endpoint that accepts a plain POST body).
2. Set `OPS_ALERT_PING_URL=<that url>` in `/etc/transit/ops-alerts.env`
   (mode 600, **not committed** — the URL itself is a bearer secret; anyone
   holding it can POST a false all-clear).
3. `sudo systemctl restart ops-alerts.timer` to pick it up.

Leaving the variable unset is fully supported: delivery is skipped and the
existing systemd exit-code signal (`systemctl --failed`, journald) is
unchanged either way — see `scripts/ops_alerts.py`'s module docstring for
the exact delivery/no-op contract, and
`tests/unit/test_ops_alerts.py`'s "ping delivery" section for the behavior
under test.

## 6. Smoke check

`scripts/ops_smoke_check.py` verifies the three collectors (`github`,
`oracle_crawler`, `r2`) are wired correctly — not that the
underlying systems they observe are currently healthy. Run it right after
install, and again after any rollback, before trusting the deployed state:

```bash
poetry run python3 scripts/ops_smoke_check.py
```

Exit 0 means all three components were returned by
`scripts/ops_status_page.collect_all` and every one validates against the
shared `scripts/ops_status.py` contract — `unknown` is an expected state
right after a fresh install and does not fail the check on its own. A
`collector_error` on one component (e.g. `gh` not authenticated yet, a cache
path that doesn't exist yet) is printed as a `WARN` line, not a failure — fix
it and re-run to confirm it clears, or leave it if it is expected on this box
(e.g. no `gh` auth configured on a throwaway host). Exit 1 means the wiring
itself is broken: a missing component, an unexpected one, or a document that
fails contract validation.

## 7. Rollback

Both units are ordinary, idempotent installs — rolling back means reversing
section 3's steps:

```bash
sudo systemctl disable --now ops-status.service ops-alerts.timer
sudo rm -f /etc/systemd/system/ops-status.service \
           /etc/systemd/system/ops-alerts.service \
           /etc/systemd/system/ops-alerts.timer
sudo systemctl daemon-reload
sudo rm -f /etc/logrotate.d/ops-monitoring
```

This does not touch `/etc/transit/ops-status.env` or
`/etc/transit/ops-alerts.env` (the secrets are left in place, so a
subsequent re-install does not need a new token) or the persisted alert
history at `/root/.ops-alert-state.json` (safe to leave — the next install's
first poll reads it as ordinary prior state, not something requiring a
clean slate). Delete either by hand only if a token itself needs rotating or
a corrupted state file needs discarding.

After rolling back, run the smoke check (section 6) against the *previous*
deployed checkout to confirm the three collectors still work there, and
`journalctl -u ops-status.service -u ops-alerts.service --since "-10min"`
to confirm no stray process from the rolled-back units is still running.

To roll back to a specific previous commit rather than removing the units
entirely, check out that commit in the deployed checkout and skip straight
to re-running the smoke check — the unit files themselves rarely need
reinstalling unless the rollback target predates this doc.

## 8. CI on an ops-monitoring PR

Nothing here is special: this hub follows the repo-wide CI rule in
`AGENTS.md`'s "Git and pull requests" section, and `transit-app-gotchas`'s
"Git" section owns how a push decides whether CI runs.

```bash
# What the gate reads. An EMPTY rollup means no run was triggered, not a pass.
gh pr view <number> --json statusCheckRollup
```

## 9. Data retention

This table is the one statement of how long each store keeps data. Every
pruning script and env var below cites it. Change a horizon here and in its
enforcer together.

| Store | Horizon | Enforced by | Setting |
|---|---|---|---|
| R2 `rt/<id>/` (raw GTFS-RT archives) | 1,825 days | `oracle_cloud/v3/bin/prune-r2.sh` | `R2_RT_RETENTION_DAYS` |
| R2 `static/<id>/` (GTFS static zips) | 3,650 days; each agency's current timetable is always kept | `prune-r2.sh` | `R2_STATIC_RETENTION_DAYS` |
| R2 `vp/<id>/` (VehiclePositions archives) | 180 days | `prune-r2.sh` | `R2_VP_RETENTION_DAYS` |
| Collector spool on Oracle (local copies of the above) | RT and VP 90 days, static 365 days; most files go sooner, once R2 confirms its copy | `oracle_cloud/v3/bin/prune.sh`, `spool-cleanup.sh` | `RETENTION_DAYS`, `STATIC_RETENTION_DAYS` |
| ClickHouse `updates`, production | every day while the prune is off (the default). With it on, 400 days, dropped in whole UTC months, and never a month whose dates are not all aggregated (below) | `gtfs_pipeline.py prune-updates` (`pipeline/retention.py`) | `--days` (default 400, constant `UPDATES_RETENTION_DAYS`); switch `updates_prune_enabled` |
| ClickHouse `updates_live` | 3 days | the table's TTL (`db/clickhouse/schema.sql`) | none |
| ClickHouse `updates_filed` (refiled rows) | follows `updates`: the dates it still holds | `pipeline/refiled.py`: whatever rewrites `updates` rows writes the affected dates off first (`rows_rewritten`), and their rows go at the agency's next analyze | none |
| ClickHouse `updates_filed_dates` | indefinite; one small row per write or write-off of a date | nothing | none |
| ClickHouse `updates`, VPS ML replica | every day synced, until the disk needs room | trimmed by hand (`docs/features/ml-forecast.md`, "Trimming it") | none |
| Postgres `agg_*`, except the one below | indefinite; analyze never rebuilds a date before the agency's retention floor (below) | nothing prunes them | none |
| Postgres `agg_route_forecast_ml` (published ML forecasts) | 14 days past each service date | `pipeline/ml_forecast_ingest.py`, on each forecast ingest (constant `FORECAST_ML_RETENTION_DAYS`) | none |
| ClickHouse aggregates (`pipeline.ch_aggregates.COPIED_TABLES` and `INTERNAL_TABLES`) | indefinite, as `agg_*` | nothing prunes them | none |

The privacy policy's periods for personal data and logs are in
`pipeline/retention.py`, which the API enforces daily.

### Why these horizons

- **R2 raw RT and static** keep years because R2 is the source everything else
  is rebuilt from. Production `updates` is rebuilt from it by catch-up, the ML
  replica replays it, and it is the open dataset's source. A static zip
  outlives the realtime days it governs because a replay has to load each
  timetable version before those days.
- **R2 VehiclePositions** are collected only to check predicted delays against
  observed passages, over a short collection window. 180 days covers the
  collection, the analysis and a rerun of it. Keeping them longer gains
  nothing, and they carry vehicle descriptors, so they are kept no longer than
  the validation needs.
- **Production `updates`** keeps 400 days once the prune is on, because a
  report's widest range is a year, plus a margin. Older days can be replayed
  from R2, and the aggregates built from them are kept indefinitely.
- **`agg_*` and the ClickHouse aggregates** are small and are what every page
  reads, so they keep all history. analyze rebuilds the ClickHouse ones from the
  same scopes as `agg_*` and copies them into Postgres.
- **ML forecasts** in Postgres fill forecast cells for the days ahead, and a
  forecast for a day long past serves nothing there, so they keep two weeks.
- **The VPS replica** is disposable and rebuilt from R2, so its disk decides.
  A replay can bring back only what R2 still holds.

### The retention floor and the `updates` prune

Each agency has a **retention floor**. `pipeline.retention.retention_floor`
says why every date before it is final. For an operator, that means:

- No analyze, incremental or full, purges or rebuilds the aggregates of those
  dates, in Postgres, in the ClickHouse aggregates or in the feed-quality
  scorecard, whose month holding the floor is final too.
- A method or builder change rebuilds only the dates from the floor on, so
  older aggregates keep the method they were built under.
- Rows that land below the floor later, from a backfill or a restamp, are never
  aggregated.
- A ClickHouse/Postgres disagreement before the floor's month is never
  repaired. A table moved to ClickHouse keeps its history before the floor's
  month in Postgres alone.
- `scripts/ch_agg_parity.py` skips its whole-history parity check for an agency
  with a floor, since its ledger no longer matches `updates` date for date.

The floor comes from `updates_pruned_months`, which only a real prune writes.
An agency with nothing dropped has no floor, and analyze behaves as it always
has. Turning the prune's switch off stops further drops but keeps every floor,
so the aggregates of the months already dropped stay.

`prune-updates` drops a UTC month of `updates` only when the whole month is
past the horizon and every agency with rows in it clears the holds
`pipeline.retention.plan_updates_prune` states. A drop raises those agencies'
floors to just past the month. The dry run names what holds each month, and
for which agency:

- service dates in the month without aggregates;
- dates up to the end of the scorecard month the new floor freezes whose
  ledger no longer matches `updates`;
- ClickHouse aggregates that disagree with their Postgres copies;
- a timetable or builder change analyze has not applied yet, or a date whose
  version in force moved since it was built.

Each clears once that agency is analyzed, except dates without aggregates
below an agency's floor: analyze never builds there, so an operator decides
whether to keep those rows or delete them.

A run that fails part-way leaves a `retention` row in `pipeline_runs` with the
rows dropped so far. The months it did not reach stay in
`updates_pruned_months` with `dropped_at` empty. Their floor stands, and the
next run drops them under a new row. So an empty `dropped_at` means only that
the run which wrote the row did not confirm the drop; ClickHouse's
`system.parts` says whether `updates` still holds the month.

Before the prune is switched on in production, each of these must hold:

- The readers that scan `updates` live refuse or flag a range before the floor,
  rather than show it as having no data: the delay certificate, the banded
  fallbacks, the trend heatmap, the Ask tools and the map.
- `db/migrate.py` and `scripts/dev_upgrade.py` treat `agg_*` as data-losing
  once `updates_pruned_months` has rows.
- An export spanning the floor records that its figures before the floor keep
  the method they were built under.

To turn it on in production:

1. Check that Railway's deployed ingest job runs no `ALTER TABLE updates
   DELETE`, such as one driven by `RETENTION_DAYS`. A delete like that sets no
   floor, so the next analyze purges the aggregates of the rows it removed.
2. Apply the migrations, and add `python gtfs_pipeline.py prune-updates` after
   `analyze_all` in the daily ingest job (`docs/deploy-railway.md`, step 5).
   With the switch off, that line drops nothing.
3. Run `python gtfs_pipeline.py prune-updates --dry-run` against production. It
   writes nothing, whatever the switch says. It lists each month past the
   horizon: the ones it would drop, with the floor each sets, and the ones it
   keeps, with the agency and date that keep each. Analyze any agency it names
   first.
4. Turn on `updates_prune_enabled` from `/admin/flags`, or set
   `UPDATES_PRUNE_ENABLED=true` on the job that runs the command.
5. After the first run, check its `retention` row on the board, and that the
   run's rows in `updates_pruned_months` have `dropped_at` set.

A dropped month's rows come back only by a replay from R2. Its aggregates need
none.
