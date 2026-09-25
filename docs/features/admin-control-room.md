# Admin control room

The `/admin/*` React routes (`RequireAdmin`-gated) and their `/api/admin/*`
backends. This file itself is served in-product by
`GET /api/admin/architecture/docs/admin-control-room` and listed by
`GET /api/admin/architecture/docs`, which glob `docs/features/*.md` at
request time — no restart needed after this file changes.

## Board

Route: `/admin` (index route under `/admin`), backed by `AdminBoardPage`,
which also renders the run timeline (`RunTimeline`) inline rather than as a
separate page.

Endpoint: `GET /api/admin/board`. Reads `agencies`, `agg_meta`, and
`agg_feed_health` for the collector tiles and freshness heatmap, plus
`pipeline_runs` for the day's run timeline (empty, not an error, when any of
these tables is absent or unreadable — each panel degrades on its own).
The four ops collectors (`vps_loop`, `github`, `oracle_crawler`, `r2`, run
via `scripts/ops_status_page.py`) run off the request path in a worker
thread under a wall-clock budget; `vps_loop` and `github` read a git
checkout at the path `OPS_STATUS_REPO` names (see `README.md` and
`.env.example`), not the database.

`POST /api/admin/runs` triggers a manual run and writes `pipeline_runs`
(see `api/admin_runs.py`).

## Agencies health + diagnostics drawer

Route: `/admin/agencies`, backed by `AdminAgenciesPage` and
`AgencyDiagnosticsDrawer`.

Endpoints:
- `GET /api/admin/agencies` — every agency including soft-deleted, for the
  list.
- `GET /api/admin/agencies/health` — fleet-wide health columns.
- `GET /api/admin/agencies/{agency_id}/diagnostics` — the drawer's full
  bundle: freshness, RT field coverage, static version timeline, clamp
  history, weather station, and the two operator-edited policy tables.
- `PATCH /api/admin/agencies/{agency_id}/standards` and
  `PATCH /api/admin/agencies/{agency_id}/weights` — edit the policy tables.
- `POST /api/admin/agencies/{agency_id}/probe` — on-demand RT coverage
  probe.
- `POST /api/admin/agencies/{agency_id}/reanalyze` — single-agency ingest +
  analyze re-run.

Tables read: `agencies`, `agg_meta`, `agg_route_daily`,
`rt_field_coverage_probes`, `agg_feed_health`, `static_trips`,
`static_calendar_dates`, `agg_static_version_summary`,
`agency_weather_stations`, `route_performance_standards`,
`ridership_weights`. The two editors write `route_performance_standards`
and `ridership_weights`; every write additionally records an
`admin_audit` entry.

## Users bulk/drawer

Route: `/admin/users` (list) and `/admin/users/:uid` (drawer overlaid via a
nested route, not a standalone page), backed by `AdminUsersPage` and
`AdminUserDetailPage`.

Endpoints:
- `GET /api/admin/users` — filtered, paged list.
- `GET /api/admin/users/{uid}` — one user's detail (drawer).
- `PATCH /api/admin/users/bulk` — role/suspend/approve a set of users at
  once.
- `PATCH /api/admin/users/{uid}` and `DELETE /api/admin/users/{uid}` —
  single-user edit and delete.
- `GET /api/admin/users/{uid}/sessions` and
  `DELETE /api/admin/users/{uid}/sessions/{sid_prefix}` — list and revoke
  active sessions, identified by a display-safe hash prefix.
- `GET /api/admin/api-keys`, `POST /api/admin/api-keys`,
  `DELETE /api/admin/api-keys/{key_id}` — API keys scoped to one user.
- `POST /api/admin/invites` — invite a new user.

Tables read/written: `users`, `sessions` (revoked on suspend/delete/role
edits that drop access), `oauth_identities`, `user_llm_keys` (read-only,
for the drawer's provider indicator), `api_keys`, `user_invites`. Every
mutation records an `admin_audit` entry.

## Audit log

Route: `/admin/audit`, backed by `AdminAuditPage`.

Endpoint: `GET /api/admin/audit` — a single cursor-paged, filterable
timeline merging `admin_audit` (every admin mutation across this whole
surface, written through `api.admin_audit.record_admin_action`) with the
login/login-failure rows of `login_events` (mapped to `login.ok`/
`login.fail`; every other `login_events` kind is already recorded directly
into `admin_audit`).

## Feature flags

Route: `/admin/flags`, backed by `AdminFlagsPage`.

Endpoints:
- `GET /api/admin/flags` — every registered flag, resolved (value, source,
  env default, override provenance).
- `PATCH /api/admin/flags/{key}` — set or replace this flag's DB override;
  `reason` is mandatory.

Tables: reads and writes `feature_flags` (one row per overridden key,
upserted — no history, only the latest reason/actor/timestamp per key);
every PATCH also records an `admin_audit` entry. See `pipeline/flags.py`
for the registry, precedence, and cache TTL, and `README.md`'s kill-switch
section for the full key list.

## Ask ops

Route: `/admin/ask`, backed by `AdminAskOpsPage`.

Endpoints:
- `GET /api/admin/ask/queries` — paged query log.
- `GET /api/admin/ask/funnel` — stage/success counts.
- `POST /api/admin/ask/promote` — promote one Stage-3 ("rag") query's
  cached intent into the RAG index ahead of the scheduled batch job's own
  hit-count/quiet-days gate.
- `GET /api/admin/ask/eval` — latest local eval artifact, or `null` when
  nothing has produced one yet.

Tables: reads `ask_query_log`; `promote` additionally reads/writes
`ask_intent_cache` and `rag_chunks` (via `pipeline.query.intent_promotion`)
and records an `admin_audit` entry.
