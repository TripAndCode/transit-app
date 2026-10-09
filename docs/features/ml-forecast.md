# ML workbench (VPS)

The VPS runs a replica of production history and the delay-forecast work on it.
The replica is rebuilt from R2 and is neither the dev stores nor production:
writes there are expected, and losing it costs only a re-sync.

## What runs

| Timer (Asia/Tokyo) | Job | Output |
|---|---|---|
| daily 10:30 | `python -m ml.cli sync` | R2 archives replayed into the replica in timeline order; done-set in `/var/lib/transit-ml/sync-state.json` |
| Sundays 04:00 | `python -m ml.cli backtest` then `report` | `/var/lib/transit-ml/backtests/latest.json`, `/var/lib/transit-ml/reports/latest/index.html` |

Each job reports to healthchecks.io through `deploy/vps/run-with-ping.sh` (`HC_PING_URL_SYNC`, `HC_PING_URL_BACKTEST`).

The sync runs `ingest` and `load_static` only. Nothing here reads `agg_*` yet, so it does not run `analyze_all`.

## Setup

1. Copy `deploy/vps/env.example` to `/etc/transit-ml/env` (root, mode 600) and fill it. The R2 token is read-only and scoped to `transit-archives`.
2. `bash deploy/vps/bootstrap.sh` from `/root/transit-app`. It is safe to re-run.

## Operating it

- View the report: `ssh -L 8000:127.0.0.1:8000 root@<vps> 'cd /var/lib/transit-ml/reports/latest && python3 -m http.server 8000 --bind 127.0.0.1'`, then open http://localhost:8000.
- A failed sync leaves that agency's later days for the next run. The done-set holds only archives whose rows are all in, so a realtime archive stays out of it until the JST day after its UTC day has ended.
- To replay from scratch: stop the timers, remove the stack together with its volumes (`docker compose -f deploy/vps/compose.yml down --volumes`), delete the done-set, and run the bootstrap again.

## Baselines

B0 is the app's "expected delay": the same route×weekday×hour, pooled by runs, over the 28 days before the forecast day. B1 is the latest such cell, and B2 is the route's 28-day mean. The report scores each on T+1..T+7 from data through T−1. It measures skill against B0 only where both predict, and shows coverage beside every error.
