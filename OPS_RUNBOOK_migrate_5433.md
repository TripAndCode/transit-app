# Ops Runbook — Migrate dev DB `:5433` (0020 → 0024) + rebuild aggregates

**Owner:** DB/ops (a human — Claude cannot run this; `:5433` is read-only to it per the DB-safety rule)
**Severity:** Medium — four read surfaces are broken on this DB until done
**Est. duration:** migration < 1 min; `analyze-all` ~10–25 min (dominated by 広島電鉄/a8, ~18M rows)
**Reversible:** Yes (migrations are additive `CREATE TABLE`; see Rollback)

---

## Why this is needed

`:5433` is stuck at migration **0020**; `main` is at **0024**. Four tables are missing. Their endpoints now degrade to a **graceful HTTP 503** `{"code":"aggregate_not_ready"}` (since PR #103) — the UI shows a calm "data not ready yet" state (PR #105) rather than the old opaque 500 / white screen. Symptom to recognize: those tabs say the data isn't prepared in this environment.

| Missing table | Migration | Breaks |
|---|---|---|
| `agg_route_stop_daily` | 0021 | 地図 (Map) heatmap **when a route filter is applied** |
| `agg_feed_health` | 0022 | **最新観測 (Live)** tab + **事業者比較 (Compare)** tab (`/api/network/summary`, `/today/route-summary`) |
| `agg_meta` | 0023 | nothing (forensic-only audit rows) — applied for completeness |
| `agg_route_hour_dow` | 0024 | **予測 (Forecast)** tab day×hour heatmap (`/api/<id>/forecast/heatmap`) — shipped by #101, after this runbook's first draft |

The other tabs (概況/地図-base/質問/レポート) already work — they read tables present at 0020. **予測 used to work at 0020 but no longer does** since #101 moved its heatmap onto `agg_route_hour_dow`.

**Both steps are required:** the migration only *creates empty* tables; the surfaces stay broken until `analyze-all` *populates* them.

---

## Pre-flight (read-only checks first)

```bash
cd /path/to/transit-app          # DATABASE_URL defaults to :5433 in the Makefile

# 1. Confirm the drift (should list 0021 0022 0023 0024, exit 1)
make check-migrations

# 2. Snapshot the migration state for the record
psql "$DATABASE_URL" -c "SELECT version FROM schema_migrations ORDER BY version;"
```

**Before proceeding, confirm:**
- [ ] You have a recent backup / PITR snapshot of `:5433` (it holds ~34M real rows and has been wiped by careless runs before — do not skip).
- [ ] No ingest/analyze job is mid-write (check for running `gtfs_pipeline.py ingest|analyze` and cron). Run during a low-traffic window.
- [ ] You are pointed at the **dev** DB, not production (`echo "$DATABASE_URL"`).

---

## Step 1 — Apply migrations 0021–0024

All four are additive `CREATE TABLE IF NOT EXISTS` — no existing data is altered or dropped.

```bash
make migrate          # = gtfs_pipeline.py migrate up; applies all pending forward
```

Verify:
```bash
make check-migrations   # should now exit 0 ("up to date")
psql "$DATABASE_URL" -c "\dt agg_route_stop_daily agg_feed_health agg_meta agg_route_hour_dow"   # all 4 exist
```

The new tables are now **empty** — the three surfaces are still broken until Step 2.

---

## Step 2 — Rebuild aggregates (populate the new tables)

`analyze-all` rebuilds every `agg_*` table for every agency from `updates`. It is output-identical for the existing tables and fills the four new ones. It is **fail-loud** (nonzero exit if any agency fails, so a partial run can't pass silently) and **heavy** — expect ~10–25 min, mostly 広島電鉄 (~18M rows).

```bash
make analyze-all 2>&1 | tee /tmp/analyze_5433.log
```

Run it in `tmux`/`screen` so a dropped SSH session doesn't kill it. If it fails on one agency, fix that agency and re-run a single one with `make analyze AGENCY_ID=<id>` (idempotent).

---

## Step 3 — Verify

```bash
# Aggregates fresh for every agency (exit 0 = all current)
make check-aggs

# The four new tables have rows
psql "$DATABASE_URL" -c "SELECT
  (SELECT count(*) FROM agg_route_stop_daily) AS route_stop_daily,
  (SELECT count(*) FROM agg_feed_health)      AS feed_health,
  (SELECT count(*) FROM agg_meta)             AS meta,
  (SELECT count(*) FROM agg_route_hour_dow)   AS route_hour_dow;"

# Endpoint smoke (anonymous, read-only) — all should be 200
#   replace 8 with a real agency_id, <code> with a real route_code, dates with a window that has data
curl -s -o /dev/null -w "Live     %{http_code}\n" "http://localhost:8000/api/8/today/route-summary"
curl -s -o /dev/null -w "Compare  %{http_code}\n" "http://localhost:8000/api/network/summary?from=YYYY-MM-DD&to=YYYY-MM-DD"
curl -s -o /dev/null -w "Map+rt   %{http_code}\n" "http://localhost:8000/api/8/delays/heatmap?from=YYYY-MM-DD&to=YYYY-MM-DD&routes=<code>"
curl -s -o /dev/null -w "Forecast %{http_code}\n" "http://localhost:8000/api/8/forecast/heatmap?route=<code>"
```

Done when `check-migrations` and `check-aggs` both exit 0 and the four endpoints return 200.

---

## Rollback

The migrations only add tables, so rollback is low-risk and rarely needed:

```bash
make migrate-down TARGET=0020     # drops agg_route_hour_dow, agg_meta, agg_feed_health, agg_route_stop_daily
```

This returns to the pre-change state (the four surfaces break again, as before). No other data is touched.

---

## Notes

- `analyze` pins the connection to JST (`Asia/Tokyo`) internally, so `captured_at::date` bucketing is correct — no extra env needed.
- Also **audit any other deployed environments** for the same 0020<0024 drift; `make check-migrations` is the one-shot probe.
- After this lands, the local `:5544` slice workaround (worktree `transit-app-live`) is no longer needed and can be torn down.
