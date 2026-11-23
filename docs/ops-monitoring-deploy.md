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
- **R2 VehiclePositions** are collected to check the feeds' predicted delays
  against observed passages ([prediction validation](features/prediction-validation.md)).
  180 days covers a validation and a rerun of it, and they carry vehicle
  descriptors, so each day is kept no longer than that. A value basis built
  from observed passages could reach back only that far, so building one is
  when to revisit this horizon.
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

Each agency has a **retention floor**, always the first day of a month.
`pipeline.retention.retention_floor` says why every date before it is final
and where the floor lies. For an operator, that means:

- No analyze, incremental or full, purges or rebuilds the aggregates of those
  dates, in Postgres, in the ClickHouse aggregates or in the feed-quality
  scorecard.
- A method or builder change rebuilds only the dates from the floor on, so
  older aggregates keep the method they were built under. Each drop records
  the method in force (`updates_pruned_months.method_version`), and a report
  or export whose period reaches dates before the floor names that method in
  its stamp. Its text notes those dates only when their method is not the
  current one, or was not recorded.
- Rows that land below the floor later, from a backfill or a restamp, are never
  aggregated.
- A ClickHouse/Postgres disagreement before the floor is never repaired. A
  table moved to ClickHouse keeps its history before the floor in Postgres
  alone.
- `scripts/ch_agg_parity.py` skips its whole-history parity check for an agency
  with a floor, since its ledger no longer matches `updates` date for date.

The floor comes from `updates_pruned_months`, which only a real prune writes.
An agency with nothing dropped has no floor, and analyze behaves as it always
has. Turning the prune's switch off stops further drops but keeps every floor,
so the aggregates of the months already dropped stay.

`prune-updates` drops a UTC month of `updates` only when the whole month is
past the horizon and every agency with rows in it clears the holds
`pipeline.retention.plan_updates_prune` states. A drop raises those agencies'
floors to the first day of the month after the next one: dropping January
sets the floor at March 1st. February's aggregates are then final while
`updates` still holds its rows. The dry run names what holds each month. One
hold applies to the month as a whole and names no agency:

- readings that file under the day before the new floor can still arrive.
  That day's readings keep arriving until 08:00 JST on the floor date, and
  only a short `--days` horizon reaches such a month. This hold clears as
  time passes; analyzing does not clear it.

The others each name the agency that holds the month:

- an agency that `analyze_all` never visits, because it is soft-deleted or
  has no `agencies` row;
- service dates in the month without aggregates;
- dates up to and including the new floor whose ledger no longer matches
  `updates`;
- ClickHouse aggregates that disagree with their Postgres copies;
- a timetable or builder change analyze has not applied yet, or a date whose
  version in force moved since it was built.

Each of these clears once that agency is analyzed, except two, where an
operator decides whether to keep the agency's rows or delete them: an agency
`analyze_all` never visits, and dates without aggregates below an agency's
floor, where analyze never builds.

Once any agency has a floor, the migration runner treats the rebuildable
aggregates like every other table (`db.migrate.data_losing_tables`): no analyze
restores their rows below the floor. `migrate up`, which every deploy runs,
refuses a pending migration that deletes, truncates or drops such rows or
columns, adds a column analyze fills, or creates such a table, and applies
nothing. The owner names each version, with `--allow-destructive-up VERSION`
or `MIGRATE_ALLOW_DESTRUCTIVE_UP=V1,V2` in the environment of the deploy that
runs it. `migrate down` refuses such a down migration, marked or not, without
`--force-destructive`.

A live report never returns or caches an answer missing rows a drop took
mid-read. Each live read checks the floor before it scans, and again once
the report's last scan has ended (`pipeline.retained_reads`). A drop always
comes after its floor is recorded, so a report that sees the old floor on
the second check finished reading before any rows went. One that sees the
new floor past its start does not answer:

- A cached report, an Ask tool and the map's look for an undated route
  view's day check before they keep or return the answer. On a refusal they
  compute once more, so a window they derived starts at the new floor. A
  requested period that now starts before it is refused.
- A snapshot checks before it is stored.
- Any other API request checks before its response is sent, and is refused
  like any read before the floor.

A run that drops a month records the new floors, then waits
`pipeline.retention.LIVE_REQUEST_DRAIN_SECONDS` before the first drop. A
live report that checked the old floor nearly always finishes in that time,
so the refusal above stays rare; the wait does not decide correctness. It holds
the affected agencies' ingest locks for that wait, so an analyze or promotion
that meets it is turned away rather than queued: the standalone poller retries
its daily step later, a cron poke is recorded `skipped`, and the CLIs exit.
`prune-updates` likewise exits when the daily step holds the lock. Schedule it
clear of that step: the poller's, soon after 00:00 and 09:00 JST, or Railway's
00:05 JST poke. A run that drops nothing does not wait.

`migrate up` reads whether a floor exists once, before it applies anything,
so the first prune that ever records a floor must not overlap a deploy.

A run that fails part-way leaves a `retention` row in `pipeline_runs` with the
rows dropped so far. The months it did not reach stay in
`updates_pruned_months` with `dropped_at` empty. Their floor stands, and the
next run drops them under a new row. So an empty `dropped_at` means only that
the run which wrote the row did not confirm the drop; ClickHouse's
`system.parts` says whether `updates` still holds the month.

To turn it on in production, which runs the `standalone` Compose profile
(`docs/deploy-standalone.en.md`):

1. Upgrade to `main` at or past the commit that adds
   `pipeline/retained_reads.py` (`docs/deploy-standalone.en.md`, section 7).
   An older build already has the prune command but not the re-check that
   keeps a live read from answering over a month it is dropping.
2. Add `prune-updates` to the host's crontab, at an hour clear of the
   poller's daily steps and of the nightly backup. cron reads the host's time
   zone; on a host set to JST, for example:

   ```cron
   30 5 * * * cd /path/to/transit-app && PATH=/usr/local/bin:/usr/bin:/bin docker compose --profile standalone exec -T app python gtfs_pipeline.py prune-updates >>"$HOME/transit-prune.log" 2>&1
   ```

   The crontab leaves it out until this step adds it, so the switch alone
   never arms a drop.
3. Run `docker compose --profile standalone exec app python gtfs_pipeline.py
   prune-updates --dry-run`. It writes nothing, whatever the switch says. It
   lists each month past the horizon: the ones it would drop, with the floor
   each sets, and the ones it keeps, with the agency and date that keep each.
   Analyze any agency it names first.
4. Turn on `updates_prune_enabled` from `/admin/flags`, or set
   `UPDATES_PRUNE_ENABLED=true` in `.env` and run `docker compose --profile
   standalone up -d app`.
5. After the first run, check its `retention` row on the board, and that the
   run's rows in `updates_pruned_months` have `dropped_at` set.

On Railway instead, promote that commit to `production`, check that the
deployed ingest job runs no `ALTER TABLE updates DELETE` (a delete sets no
floor, so the next analyze purges the aggregates of the rows it removed), and
add `python gtfs_pipeline.py prune-updates` after `analyze_all` in that job
(`docs/deploy-railway.md`, step 5); the dry run, switch and check are the same.

A dropped month's rows come back only by a replay from R2. Its aggregates need
none.
