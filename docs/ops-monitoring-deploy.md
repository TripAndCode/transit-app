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
is provisioned and owned as `root`, and `claude-loop.service` already runs
as `root` against the same checkout). Splitting these two units onto a
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
  CLI, reusing whatever `gh auth` session already exists for
  `/vps-loop-run` — no separate token, and no new scope requested.
- **Oracle** (`scripts/collect_oracle_status.py`): reads a heartbeat
  relayed through GitHub Actions run logs. The VPS never holds an Oracle
  credential of any kind, and specifically never the Oracle SSH private key
  (see item 119's design) — a compromised VPS gains nothing toward Oracle.
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
collector side: a bare ping to the configured URL means "quiet poll", a ping
to `<url>/fail` (carrying the rendered alert text as the POST body) means
"something is wrong". To set it up:

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

`scripts/ops_smoke_check.py` verifies the four collectors (`vps_loop`,
`github`, `oracle_crawler`, `r2`) are wired correctly — not that the
underlying systems they observe are currently healthy. Run it right after
install, and again after any rollback, before trusting the deployed state:

```bash
poetry run python3 scripts/ops_smoke_check.py
```

Exit 0 means all four components were returned by
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
deployed checkout to confirm the four collectors still work there, and
`journalctl -u ops-status.service -u ops-alerts.service --since "-10min"`
to confirm no stray process from the rolled-back units is still running.

To roll back to a specific previous commit rather than removing the units
entirely, check out that commit in the deployed checkout and skip straight
to re-running the smoke check — the unit files themselves rarely need
reinstalling unless the rollback target predates this doc.

## 8. `[skip ci]` verification

Every commit in a PR touching this monitoring hub must carry `[skip ci]`
(see CLAUDE.md's "Git and pull requests" section — CI is skipped repo-wide,
so this is a hard requirement, not specific to ops changes). Before opening
or readying such a PR:

```bash
git log --format='%H %s%n%b' origin/main..HEAD | grep -c '^\[skip ci\]$\|\[skip ci\]'
git log --format='%H' origin/main..HEAD | wc -l
```

Confirm the two counts imply every commit has the trailer (each commit's
subject+body must contain the literal line), and double check the branch's
actual pushed tip commit specifically — a merge commit from resolving a
`main` conflict does not get `[skip ci]` automatically from `git merge` and
must have it added by hand (see `transit-app-gotchas`'s "Git" section for
the exact failure mode this guards against).
