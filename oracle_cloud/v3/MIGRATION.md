# Collector v3 migration runbook

Spec: docs/superpowers/specs/2026-06-06-collector-v3-design.md
Aomori RT gap: < 1 minute (step 4). Old tree left intact 1 week as fallback.

## Important behavior change: Aomori static GTFS
Agency 1 (Aomori) has NO direct static zip URL — its `static_url` is an
opendata index page consumed by the `aomori_index_scrape` strategy in the
Python pipeline. The v3 VM collector therefore does NOT fetch Aomori static
(empty `static_url` column in `agencies.tsv`). After cutover, Aomori static is
collected workstation-side by `gtfs_pipeline.py refresh-static` /
`make fetch-ingest`, which scrape the source directly. Hiroshima (8/9/10)
static IS collected on the VM via `direct_url` curl.

## 0. Prereqs
- [ ] Create 4 checks at https://healthchecks.io (period 10 min, grace 5 min); note ping URLs.
- [ ] Create a 5th "collector health" check (period 1 h, grace 30 min) for
      `ALERT_PING_URL` — see [11. Alerting](#11-alerting).
- [ ] `feat/collector-v3` merged; `oracle_cloud/v3/` present on workstation.

## 1. Install tree (no impact on running v1)
    # from workstation repo root:
    KEY=oracle_cloud/ssh-key-2026-03-28.key; VM=opc@64.110.114.101
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
    crontab -l > /tmp/crontab.backup.$(date +%s)
    crontab /home/opc/crontab.snippet   # prune line stays commented

## 6. Start Hiroshima
    sudo systemctl enable --now rt-poller@8 rt-poller@9 rt-poller@10
    journalctl -u rt-poller@8 -n 5

## 7. Workstation: v3 fetch + parity gate
    # .env: add COLLECTOR_DATA_DIR=/home/opc/collector/data
    # FIRST move the pre-existing local flat mirror into per-agency dirs:
    mkdir -p raw_archives/1 && mv raw_archives/*.tar.gz raw_archives/1/ 2>/dev/null || true
    mkdir -p raw_archives_static/1 && mv raw_archives_static/*.zip raw_archives_static/1/ 2>/dev/null || true
    make fetch
    # parity check (counts must match):
    ssh -i $KEY $VM 'ls /home/opc/collector/data/1/rt/*.tar.gz | wc -l'
    ls raw_archives/1/*.tar.gz | wc -l

## 8. Verify cron fired (next morning JST, on VM)
    tail -20 /home/opc/collector/cron.log     # expect static-fetch + rotate lines
    ls /home/opc/collector/data/1/rt/         # yesterday tarred, today live dir
    systemctl status 'rt-poller@*' --no-pager # all active

## 9. Enable prune (ONLY after step 7 parity + step 8 verification)
    crontab -e   # uncomment the prune line

## 9b. Direct-to-R2 sync (supersedes step 7's manual gate)
`bin/sync-r2.sh` now runs daily in cron (see `crontab.snippet`), mirroring
this VM's own `data/<id>/{rt,static}` straight to Cloudflare R2 — no more
manual `make fetch` + `make sync-r2` from a workstation as prune's parity
gate. `prune.sh` now also refuses to run (exit 65) unless sync-r2.sh's
`.sync-r2.last-ok` marker is fresh, so the parity gate is enforced in code,
not just documented here.

`bin/verify-r2.sh` runs right after `sync-r2.sh` and independently lists
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
(`rt/<id>/<YYYYMMDD>.tar.gz`, `static/<id>/gtfs_static_<YYYYMMDD>.zip`) --
neither case is ever pruned by `prune-r2.sh`, since it only ever looks at
prefixes for ids still in the current roster. It is dry-run by default (a
nonzero exit when it finds orphans, so `cron-wrap.sh` pages on it, but nothing
is deleted); deleting requires an operator to rerun it by hand with
`--execute` (or `RECONCILE_R2_EXECUTE=1`), which itself still refuses unless
`sync-r2.sh`'s success marker is fresh, and reconfirms each object individually
right before its own delete.

`bin/spool-cleanup.sh` runs daily right after `verify-r2.sh` and reclaims
local disk file by file, the moment its own R2 listing confirms (byte-size
match) that specific RT/static archive is uploaded — well before
`prune.sh`'s much longer age-based window. A failed or partially uploaded
archive is left in place for `sync-r2.sh` to retry; local spool bytes
remaining after that reclaim are checked against the required
`SPOOL_DISK_BUDGET_BYTES` env var, and exceeding it is reported as a
failure, never as a further deletion. `spool-cleanup.sh`'s crontab.snippet
entry runs immediately once the snippet is installed, so `SPOOL_DISK_BUDGET_BYTES`
must be set in `/etc/environment` alongside the `OBJECT_STORE_*` vars *before*
running `crontab /home/opc/crontab.snippet` (step 5), not after.

Known gap, unchanged from before: agency 1 (Aomori) has no `static_url` in
`agencies.tsv` (see the note at the top of this file), so this VM never
collects new Aomori static GTFS at all — sync-r2.sh only mirrors what the
VM actually has, so agency 1's R2 `static/1/` prefix stays exactly as
stale as `data/1/static/` on the VM itself. Check `agencies.tsv` and
`data/1/static/latest.zip`'s own mtime directly if you need to know how
stale that specifically is right now — don't trust a date recorded here,
since this file isn't updated when that staleness changes. Aomori's static
GTFS still needs its own refresh path if that gap matters.

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

`cron-wrap.sh` wraps every cron job and pings `<url>/fail` with the job's exit
status and output tail when one fails. `health-check.sh` runs every 30 min and
pings the bare URL when everything is fresh, `<url>/fail` with the specific
reasons when it is not. Leaving `ALERT_PING_URL` unset is supported and keeps
the previous log-only behavior — nothing fails, nothing pages.

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
intentionally turned off) still runs every other cron job exactly as before
— nothing else in `crontab.snippet` depends on either of these two new
lines succeeding.

## Rollback (any point before step 10)
    sudo systemctl disable --now 'rt-poller@1' 'rt-poller@8' 'rt-poller@9' 'rt-poller@10'
    crontab /tmp/crontab.backup.*    # restores @reboot v1 line
    mv /home/opc/collector/data/1/rt/*.tar.gz /home/opc/app/transportation_analysis/archive/ 2>/dev/null
    nohup nice -n 10 /home/opc/app/transportation_analysis/poller.sh >> /home/opc/app/transportation_analysis/cron.log 2>&1 & disown
