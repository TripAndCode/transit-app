# Collector v3 migration runbook

Aomori RT gap: < 1 minute (step 4). Old tree left intact 1 week as fallback.

## Important behavior change: Aomori static GTFS
Agency 1 (Aomori) has NO direct static zip URL — its `static_url` is an
opendata index page consumed by the `aomori_index_scrape` strategy in the
Python pipeline. The v3 VM collector therefore does NOT fetch Aomori static
(empty `static_url` column in `agencies.tsv`). After cutover, a new Aomori
timetable comes only from `refresh_static`, which scrapes the source directly:
`poetry run python gtfs_pipeline.py refresh-static --agency-id 1`, or the admin
control room's refresh-static action, which runs the same code. Never from the
VM. `make fetch-ingest` does not refresh it either: it loads the zips already
mirrored from the VM, which never receives a new Aomori one.
Hiroshima (8/9/10) static IS collected on the VM via `direct_url` curl.

## 0. Prereqs
- [ ] Create 4 checks at https://healthchecks.io (period 10 min, grace 5 min); note ping URLs.
- [ ] Create a 5th "collector health" check (period 1 h, grace 30 min) for
      `ALERT_PING_URL` — see [11. Alerting](#11-alerting).
- [ ] `oracle_cloud/v3/` present on workstation.

## 1. Install tree (no impact on running v1)
    # from workstation repo root:
    KEY=~/.ssh/<key>; VM=opc@<oci-public-ip>
    ssh -i $KEY $VM 'mkdir -p /home/opc/collector/{etc,bin,data}'
    scp -i $KEY oracle_cloud/v3/bin/* $VM:/home/opc/collector/bin/
    scp -i $KEY oracle_cloud/v3/etc/agencies.tsv.example $VM:/home/opc/collector/etc/agencies.tsv
    scp -i $KEY oracle_cloud/v3/rt-poller@.service oracle_cloud/v3/logrotate-collector.conf oracle_cloud/v3/crontab.snippet $VM:/home/opc/
    ssh -i $KEY $VM 'chmod +x /home/opc/collector/bin/*.sh'
    # on VM: edit etc/agencies.tsv — replace FILL-ME ping URLs; verify TABs survived:
    ssh -i $KEY $VM "awk -F'\t' '!/^#/ && NF!=6 {print \"BAD ROW: \" \$0}' /home/opc/collector/etc/agencies.tsv"

## 2. Move existing data (instant, same filesystem)
    ssh -i $KEY $VM
    cd /home/opc/app/transportation_analysis
    mkdir -p /home/opc/collector/data/1/{rt,static}
    mv archive/2026*.tar.gz /home/opc/collector/data/1/rt/
    mv static_archive/gtfs_static_*.zip /home/opc/collector/data/1/static/
    cd /home/opc/collector/data/1/static && ln -sfn "$(ls gtfs_static_*.zip | sort | tail -1)" latest.zip

## 3. Install systemd unit + logrotate (sudo, on VM)
    sudo cp /home/opc/rt-poller@.service /etc/systemd/system/
    sudo cp /home/opc/logrotate-collector.conf /etc/logrotate.d/collector
    sudo systemctl daemon-reload

## 4. Cutover Aomori (the <1 min gap)
    OLD_PID=$(pgrep -f 'transportation_analysis/poller.sh' | head -1)
    [ -n "$OLD_PID" ] && kill "$OLD_PID"
    today=$(date -u +%Y%m%d)
    [ -d "/home/opc/app/transportation_analysis/archive/$today" ] && \
        mv "/home/opc/app/transportation_analysis/archive/$today" /home/opc/collector/data/1/rt/
    sudo systemctl enable --now rt-poller@1
    journalctl -u rt-poller@1 -n 5   # expect OK lines

## 5. Replace crontab (on VM)
Every job in the snippet is live once installed, `prune.sh`, `prune-r2.sh` and
`spool-cleanup.sh` included, so set `OBJECT_STORE_*` and
`SPOOL_DISK_BUDGET_BYTES` in `/etc/environment` first (see 9b).

    crontab -l > /tmp/crontab.backup.$(date +%s)
    crontab /home/opc/crontab.snippet

## 6. Start Hiroshima
    sudo systemctl enable --now rt-poller@8 rt-poller@9 rt-poller@10
    journalctl -u rt-poller@8 -n 5

## 7. Workstation: per-agency mirror layout
    # .env: add COLLECTOR_DATA_DIR=/home/opc/collector/data
    # Move the pre-existing local flat mirror into per-agency dirs:
    mkdir -p raw_archives/1 && mv raw_archives/*.tar.gz raw_archives/1/ 2>/dev/null || true
    mkdir -p raw_archives_static/1 && mv raw_archives_static/*.zip raw_archives_static/1/ 2>/dev/null || true
    make fetch

`make fetch` copies only what is still on the VM. `spool-cleanup.sh` deletes each
archive there once its own R2 listing confirms it, so VM and workstation counts
stop matching after its first run, and comparing them is not a parity check. The
weekly prune is the step gated on `sync-r2.sh` having succeeded (step 9 and 9b).

## 8. Verify cron fired (next morning JST, on VM)
    tail -20 /home/opc/collector/cron.log     # expect static-fetch + rotate lines
    ls /home/opc/collector/data/1/rt/         # yesterday tarred, today live dir
    systemctl status 'rt-poller@*' --no-pager # all active

## 9. Prune
There is nothing to enable: step 5 installed `prune.sh` and `prune-r2.sh` live,
gated on `sync-r2.sh`'s success marker (9b). Before their first Sunday run,
confirm the marker is from this morning:

    ls -l /home/opc/collector/.sync-r2.last-ok

## 9b. Direct-to-R2 sync
`bin/sync-r2.sh` runs daily in cron (see `crontab.snippet`), mirroring
this VM's own `data/<id>/{rt,vp,static}` straight to Cloudflare R2 as prune's
parity gate. `prune.sh` refuses to run (exit 65) unless sync-r2.sh's
`.sync-r2.last-ok` marker is fresh, so the parity gate is enforced in code,
not just documented here.

`bin/verify-r2.sh` runs right after `sync-r2.sh`, waits for a sync still in
progress to release its lock (a problem in itself past
`VERIFY_R2_SYNC_WAIT_SECONDS`, default one hour), and then independently lists
each configured agency's actual R2 objects, checking freshness and
byte-size integrity against local disk instead of trusting `sync-r2.sh`'s
own exit status. `bin/prune-r2.sh` runs weekly (like `prune.sh`, gated on
the same fresh-marker check) and enforces a bounded, multi-year retention
window on R2 objects — see that script for why its defaults are so much
longer than `prune.sh`'s local ones. Both are wrapped by `cron-wrap.sh` like
`sync-r2.sh` and `prune.sh`, so a failure of either pages the same way.

`bin/reconcile-r2.sh` runs weekly, right after `prune-r2.sh`, and covers what
that age-based sweep cannot: it lists the ENTIRE bucket and flags any object
under an agency id no longer in `agencies.tsv` (a retired agency, a renumbered
or corrected id), or one whose filename doesn't match the naming rule at all
(`rt/<id>/<YYYYMMDD>.tar.gz`, `vp/<id>/<YYYYMMDD>.tar.gz`,
`static/<id>/gtfs_static_<YYYYMMDD>.zip`) --
neither case is ever pruned by `prune-r2.sh`, since it only ever looks at
prefixes for ids still in the current roster. It is dry-run by default (a
nonzero exit when it finds orphans, so `cron-wrap.sh` pages on it, but nothing
is deleted); deleting requires an operator to rerun it by hand with
`--execute` (or `RECONCILE_R2_EXECUTE=1`), which itself still refuses unless
`sync-r2.sh`'s success marker is fresh, and reconfirms each object individually
right before its own delete.

`bin/spool-cleanup.sh` runs daily right after `verify-r2.sh` and reclaims
local disk file by file, the moment its own R2 listing confirms (byte-size
match) that specific RT, VP or static archive is uploaded — well before
`prune.sh`'s much longer age-based window. A failed or partially uploaded
archive is left in place for `sync-r2.sh` to retry; local spool bytes
remaining after that reclaim are checked against the required
`SPOOL_DISK_BUDGET_BYTES` env var, and exceeding it is reported as a
failure, never as a further deletion. `spool-cleanup.sh`'s crontab.snippet
entry runs immediately once the snippet is installed, so `SPOOL_DISK_BUDGET_BYTES`
must be set in `/etc/environment` alongside the `OBJECT_STORE_*` vars *before*
running `crontab /home/opc/crontab.snippet` (step 5), not after.

Known gap: agency 1 (Aomori) has no `static_url` in
`agencies.tsv` (see the note at the top of this file), so this VM never
collects new Aomori static GTFS at all — sync-r2.sh only mirrors what the
VM actually has, so agency 1's R2 `static/1/` prefix stays exactly as
stale as `data/1/static/` on the VM itself. Check `agencies.tsv` and
`data/1/static/latest.zip`'s own mtime directly if you need to know how
stale that specifically is right now — don't trust a date recorded here,
since this file isn't updated when that staleness changes. Aomori's static
GTFS still needs its own refresh path if that gap matters.

## 9c. Continuous Oracle -> application ingest

Each poller can push a successful protobuf to the running API every
`COLLECTOR_INGEST_INTERVAL_SEC` (default 300 s; set it equal to the poll
interval to push every poll). Set
the same secret on the API and Oracle, and set the Oracle URL to the API's
reachable base path (without `/updates`):

    # API environment
    COLLECTOR_INGEST_SECRET=<generate-a-long-random-secret>

    # Oracle /etc/environment
    COLLECTOR_INGEST_URL=https://<api-host>/internal/collector
    COLLECTOR_INGEST_SECRET=<same-secret>

Then reinstall the unit, reload systemd, and restart all active pollers:

    sudo cp /home/opc/rt-poller@.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl restart 'rt-poller@*'

Each 2xx response means the payload reached `updates_live`, not `updates`:
history comes from the daily promotion (`POST /internal/cron/ingest`), which
copies each closed JST day out of `updates_live` into `updates`/`agg_*`. A
failed push is logged but does not stop polling; the next due push
supplies a new snapshot. The endpoint is authenticated and deduplicates
retries by the collector's source filename.

## 10. +1 week: remove old tree
    rm -rf /home/opc/app/transportation_analysis/{poller.sh,poller_static.sh,cron.log,poller.log,static_poller.log,static_cron.log,archive,static_archive}

## 11. Alerting
The 4 per-agency checks in step 0 only cover RT polling, and only for an
agency whose poller is running well enough to ping. Everything else — static
fetching, the R2 mirror, empty output, an agency that is configured but never
started — is covered by `bin/health-check.sh` plus `bin/cron-wrap.sh`, both of
which report to the single "collector health" check:

    # on VM, /etc/environment (cron does not source ~/.bashrc):
    ALERT_PING_URL=https://hc-ping.com/<collector-health-uuid>

`cron-wrap.sh` wraps every cron job except `health-check.sh` and pings
`<url>/fail` with the job's exit status and output tail when one fails.
`health-check.sh` runs every 30 min, unwrapped, and
pings the bare URL when everything is fresh, `<url>/fail` with the specific
reasons when it is not. Leaving `ALERT_PING_URL` unset is supported and keeps
the collector log-only — nothing fails, nothing pages.

Thresholds are env-overridable in `/etc/environment` (defaults in
`health-check.sh`); `SYNC_R2_MAX_STALE_DAYS` is deliberately shared with
`prune.sh`, so the R2 staleness that stops pruning is the same one that alerts.

Verify before relying on it:

    /home/opc/collector/bin/health-check.sh; echo "exit=$?"     # expect exit=0
    ALERT_PING_URL= COLLECTOR_BASE=/tmp/nope /home/opc/collector/bin/health-check.sh; echo "exit=$?"
    # expect exit=64 (missing roster) and the reason on stderr, nothing pinged

## 12. Operations-status heartbeat
`bin/status-snapshot.sh` (cron, every 30 min) builds one operations-status
document (see `scripts/ops_status.py` in the main repo checkout for the
contract) from the same on-disk evidence `health-check.sh`/`verify-r2.sh`
already produce — RT/static freshness, local disk usage, the `.sync-r2.last-
ok` marker, and two markers `verify-r2.sh` writes: `.verify-r2.last-result`
(`<ISO8601> <ok|fail> <object_count>`, written on every completed run,
success or failure — this drives `verify_result`/the `failed` state) and
`.verify-r2.last-success` (a bare `<ISO8601>` timestamp, written only when a
run succeeds). The document's `last_success_at`/`age_seconds` for this
subsystem always come from the success-only marker, never from
`.last-result`'s own timestamp — otherwise a failed run's own completion time
would masquerade as a success. When there is no genuine prior success on
record (a first-ever failure, or the marker was never written), the document
reports `last_success_at: null`/`age_seconds: null` alongside `state:
"failed"`, the same way `rt_state`/`static_state` report `null`/`unknown` for
"no evidence yet" — and writes it atomically to
`$COLLECTOR_BASE/.status/oracle-crawler-status.json`. Unlike `health-check.sh` it
never pages on its own; an unhealthy *reported* state is the normal, valid
output of a successful run, not a script failure.

`bin/publish-status.sh` (cron, right after it) sends that document to GitHub
as a `repository_dispatch` event, authenticated with `ORACLE_STATUS_GH_TOKEN`
— a fine-grained personal access token for this repo. The `dispatches`
endpoint's only available grant is repository Contents: read & write, the
same permission needed to push commits or write/delete files via the
Contents API, so this token is push-equivalent access to this repo, not a
heartbeat-only capability, and is held and rotated with that same rigor.
This is HTTPS end to end; nothing about
this channel ever needs Oracle's own SSH private key (or a copy of it) to
exist anywhere else, and the VPS reads the result back out via its own,
already-configured `gh` authentication (see `.github/workflows/oracle-
heartbeat-listener.yml` and `scripts/collect_oracle_status.py`) — no new
credential is needed on the VPS side at all.

Provisioning: add `ORACLE_STATUS_GH_TOKEN=<token>` to `/etc/environment`
alongside the other secrets already documented above. Leaving it unset is a
supported configuration: `publish-status.sh` logs that publishing is
disabled and exits 0, so a VM without a token (or one where this channel is
intentionally turned off) still runs every other cron job — nothing else in
`crontab.snippet` depends on the `status-snapshot.sh` or `publish-status.sh`
line succeeding.

## 13. Storage metrics heartbeat
`bin/storage-metrics.sh` (cron, every 30 min, offset from status-snapshot.sh)
builds a second operations-status document, component `"r2"`, reporting this
VM's own filesystem usage (`df` on `COLLECTOR_BASE`) and Cloudflare R2's
object count/bytes split by the `rt/`, `static/` and `vp/` prefixes (`vp/`
is empty until a VehiclePositions day is uploaded, which reads as zero
objects, not a failed listing), each classified
against configurable warning/critical thresholds
(`DISK_WARN_PCT`/`DISK_CRIT_PCT`, default 80/90; `R2_BYTES_WARN_THRESHOLD`/
`R2_BYTES_CRIT_THRESHOLD`, default 50/100 GiB). Unlike `status-snapshot.sh`,
this DOES need `OBJECT_STORE_*` credentials of its own — nothing else in
`bin/` already computes a whole-bucket object count/byte total — but leaving
them unset is a supported configuration (`r2_state` reports
`not_applicable` rather than dragging the document down), so a VM that
hasn't been given R2 credentials yet still runs this job without error. Each
prefix is listed with a single `aws s3 ls --recursive` call (mirroring
`spool-cleanup.sh`'s own `r2_list()`), which paginates the whole prefix
internally with no manual `NextToken` bookkeeping; object count and byte
total come from summing that listing's own `<date> <time> <size> <key>`
lines. A listing failure on one prefix reports `r2_state=failed` with that
prefix's numbers null (its captured stderr logged alongside), without hiding
behind the other prefix's success. `.storage-metrics.last-success` records
the last time both prefixes listed successfully, so a failed run still
reports the last genuine success's timestamp rather than either fabricating
a fresh one or losing it.

The same `bin/publish-status.sh` used for the oracle_crawler heartbeat above
sends this document to GitHub too — pointed at `storage-metrics.sh`'s own
output file and a distinct event type via `ORACLE_STATUS_FILE`/
`ORACLE_STATUS_EVENT_TYPE` (see `crontab.snippet`), not a second publisher
script. `.github/workflows/r2-storage-heartbeat-listener.yml` and
`scripts/collect_r2_status.py` are the GitHub-side and VPS-side legs,
structurally identical to the oracle_crawler channel's own
`oracle-heartbeat-listener.yml`/`collect_oracle_status.py` but keyed on
component `"r2"` and their own independent replay watermark.

## 14. VehiclePositions validation window
Every delay figure in the app is a feed's TripUpdates *prediction*
(`dep_delay`), never an observed departure. To measure how far those
predictions are from what the buses did, the collector can archive GTFS-RT
VehiclePositions for a bounded window (7–14 days), from which observed stop
passages are later reconstructed from positions near each stop. The analysis
is tracked in issue #1503. Nothing in the app ingests these samples: the
workstation's `make fetch` copies only `rt/` and `static/` from the VM, and
`ml/sync.py` and `scripts/catch_up_from_r2.py` list only the `rt/` and
`static/` prefixes in R2.

Where the samples land:
- On the VM: `data/<id>/vp/<UTCDAY>/VehiclePosition_HHMMSS.pb`, one raw
  FeedMessage per poll, written by `bin/vp-poller.sh` (systemd
  `vp-poller@<id>`). It never pushes to the app.
- `rotate-day.sh` tars each closed UTC day to `data/<id>/vp/<YYYYMMDD>.tar.gz`
  (holding `<YYYYMMDD>/VehiclePosition_*.pb`), exactly as it does `rt/`.
- In R2: `vp/<id>/<YYYYMMDD>.tar.gz`, beside `rt/<id>/`.

What each job does with them:
- `rotate-day.sh`, `sync-r2.sh`, `spool-cleanup.sh` and `prune.sh` act on
  every `data/*/vp` directory on disk, the way they act on `rt/`, so the
  last day still reaches R2 after the window ends. A failed `vp/` upload fails
  `sync-r2.sh` like a failed `rt/` one, which withholds `.sync-r2.last-ok`
  and pauses `prune.sh` until a later run succeeds.
- `health-check.sh` and `verify-r2.sh` check only the feeds listed in
  `etc/vehicle_positions.tsv`, with the RT thresholds: a sample younger than
  interval × `RT_STALE_FACTOR`, and a fresh, size-matched day in `vp/<id>/`.
- `reconcile-r2.sh` and `prune-r2.sh` treat `vp/<id>/` as expected, on the RT
  retention, for any id in `agencies.tsv`, so ending the window never turns
  the archive into orphans.
- `storage-metrics.sh` reports `vp_object_count`/`vp_bytes` and counts them in
  `r2_bytes_total`. `status-snapshot.sh` leaves VP out.

### Enable
`etc/vehicle_positions.tsv` lists the feeds (`id, name, interval, url`,
tab-separated); every id must also be an `agencies.tsv` row. An agency
without a row runs no VP poller, and with no file at all nothing above
changes. Deploy `bin/` and the files outside 09:00–09:45 JST, while no
daily cron job is running from `bin/`, then install the unit:

    # from workstation repo root (KEY/VM as in step 1):
    scp -i $KEY oracle_cloud/v3/bin/* $VM:/home/opc/collector/bin/
    scp -i $KEY oracle_cloud/v3/etc/vehicle_positions.tsv.example $VM:/home/opc/collector/etc/vehicle_positions.tsv
    scp -i $KEY oracle_cloud/v3/vp-poller@.service $VM:/home/opc/
    ssh -i $KEY $VM 'chmod +x /home/opc/collector/bin/*.sh'

    # on VM: TABs survived, and each url answers 200 with a nonzero body.
    # Delete any row that does not before enabling its unit.
    awk -F'\t' '!/^#/ && NF!=4 {print "BAD ROW: " $0}' /home/opc/collector/etc/vehicle_positions.tsv
    for u in $(awk -F'\t' '!/^#/ && NF==4 {print $4}' /home/opc/collector/etc/vehicle_positions.tsv); do
        curl -s -o /dev/null -w "%{http_code} %{size_download} $u\n" "$u"
    done

    sudo cp /home/opc/vp-poller@.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now vp-poller@8 vp-poller@9 vp-poller@10
    journalctl -u vp-poller@8 -n 5    # expect "OK VehiclePosition_HHMMSS.pb" lines

    # The new bin/rt-poller.sh fetches through bin/poll-lib.sh, with the same
    # behaviour; restart the RT pollers so it runs now rather than at the next reboot.
    sudo systemctl restart 'rt-poller@*'
    journalctl -u rt-poller@8 -n 5

Enable a unit in the same sitting as its row: `health-check.sh` pages on a
listed feed with no `vp/` directory. Avoid 09:00–09:15 JST, when no closed VP
day exists yet for `verify-r2.sh`'s 09:15 run to find. The next morning,
`cron.log` should show `rotated .../vp/<day>.tar.gz` and `[a8] vp synced`.

### Stop
When the window ends, stop the pollers and delete the rows (or the whole
`etc/vehicle_positions.tsv`) right away, before `health-check.sh` pages on
the stopped feeds:

    sudo systemctl disable --now vp-poller@8 vp-poller@9 vp-poller@10
    rm /home/opc/collector/etc/vehicle_positions.tsv

The next morning's cron run tars the last partial day, uploads it to
`vp/<id>/` and reclaims the local copies once R2 confirms them. Once
`data/<id>/vp/` is empty, remove those directories, or `sync-r2.sh` keeps
syncing them daily and a transient failure there still withholds its success
marker. `rmdir` refuses a directory that still holds an unconfirmed day:

    rmdir /home/opc/collector/data/8/vp /home/opc/collector/data/9/vp /home/opc/collector/data/10/vp

The archive stays in R2:

    aws s3 ls "s3://$OBJECT_STORE_BUCKET/vp/" --recursive --endpoint-url "$OBJECT_STORE_ENDPOINT"

## Rollback (any point before step 10)
    sudo systemctl disable --now 'rt-poller@1' 'rt-poller@8' 'rt-poller@9' 'rt-poller@10'
    crontab /tmp/crontab.backup.*    # restores @reboot v1 line
    mv /home/opc/collector/data/1/rt/*.tar.gz /home/opc/app/transportation_analysis/archive/ 2>/dev/null
    nohup nice -n 10 /home/opc/app/transportation_analysis/poller.sh >> /home/opc/app/transportation_analysis/cron.log 2>&1 & disown
