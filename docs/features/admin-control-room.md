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
Its staleness alerts come from `pipeline.health.aggregate_freshness`, the
check `GET /api/admin/ops` reads, so the board and Ops always name the same
agencies as behind; agencies never analyzed share one alert. The board holds
that answer for a few minutes per process, since staleness moves by whole
days and the page polls every few seconds; Ops reads it live.
The three ops collectors (`github`, `oracle_crawler`, `r2`, run
via `scripts/ops_status_page.py`) run off the request path in a worker
thread under a wall-clock budget; `github` reads a git
checkout at the path `OPS_STATUS_REPO` names (see `README.md` and
`.env.example`), not the database.

`POST /api/admin/runs` triggers a manual run and writes `pipeline_runs`
(see `api/admin_runs.py`).

Alert center: every admin page's header carries a bell (`AlertCenter`) over
the same board snapshot, with an unread count and a popover of its alerts
grouped by level. On `/admin` it adds no poll of its own (the board page
polls); elsewhere it polls the endpoint every minute. Acknowledging an alert
is shared by every admin on every device: `POST
/api/admin/board/alerts/{key}/ack` stores it for 7 days in `admin_alert_acks`
and audits it as `board_alert.ack`, and the board returns each alert's `key`
(`api.admin_board.alert_key`, over its level, code, params and link) with
whether it is `acked`. An alert whose figure changes (a lag that grows by a
day, a count that moves) gets a new key and reads as a new alert.

## Agencies health + diagnostics drawer

Route: `/admin/agencies`, backed by `AdminAgenciesPage`,
`AgencyDiagnosticsDrawer` and `AgencyRuns` (the stage cells, the step chain
and the run card).

Endpoints:
- `GET /api/admin/agencies` — every agency including soft-deleted, for the
  list.
- `GET /api/admin/agencies/health` — fleet-wide health columns, plus each
  agency's timetable load day, whether a job is in flight for it, and its
  latest operator run step by step (`latest_run`). The page polls it every
  3s while an operator's run is in flight, every 30s while only another job
  (usually the cron sweep) holds an agency, and not at all otherwise.
- `GET /api/admin/agencies/{agency_id}/diagnostics` — the drawer's full
  bundle: freshness, RT field coverage, static version timeline, clamp
  history, weather station, and the two operator-edited policy tables.
- `PATCH /api/admin/agencies/{agency_id}/standards` and
  `PATCH /api/admin/agencies/{agency_id}/weights` — edit the policy tables.
- `POST /api/admin/agencies/{agency_id}/probe` — on-demand RT coverage
  probe.
- `POST /api/admin/agencies/{agency_id}/reanalyze` — single-agency ingest +
  analyze re-run.
- `POST /api/admin/agencies/{agency_id}/refresh-static` — reload the
  agency's timetable (static GTFS) through `refresh_static`, the function
  behind `gtfs_pipeline.py refresh-static`.
- `POST /api/admin/agencies/{agency_id}/update` — timetable reload, then
  ingest + analyze, as one background job that stops at its first failed
  step. The add form's 保存してセットアップ saves and then starts it.

The three run actions open an umbrella `pipeline_runs` row (`ingest`,
`static` or `update`) and file each step's row under it (`parent_run_id`).
A timetable step closes `ok` when it loaded a zip, `unchanged` when the
source had nothing newer, and `error` when the source could not be fetched
(an HTTP error, a network error, a URL that is not a valid http(s) URL or that
the guard rejected, an index page with no timetable link, a download that is
not a zip), with that reason on the run card; only `ok` moves the
load day. Each action answers 409 while any job is working on the agency, for
a disabled agency, and (the two timetable actions) for an agency with no
static URL or static strategy. The board's
`POST /api/admin/runs` takes the same guard when scoped to one agency.

"Nothing newer" means nothing newer than the zip that last loaded, as the
strategy's change manifest records it (`raw_archives_static/<agency_id>/_manifest.json`:
the loaded zip's sha256, and for `direct_url` the ETag/Last-Modified its
conditional GET sends).
That file can outlive the timetable tables, after a database restore or once
an agency's static rows are cleared, so a reload for an agency with no
timetable loaded sets the manifest aside and loads whatever the source serves.
If that still loads nothing, because the source sent no zip or the agency's
static URL or strategy was cleared after the press, the step closes `error`
rather than `unchanged`, and a chained update stops there.
The update runs the timetable step first because a `static_join` agency's
ingest step closes `error`, naming the timetable as the step to run, while the
agency has none loaded. The reanalyze action and an ingest run from the board
have no timetable step of their own, so for such an agency they record that
error.
For an agency that does have one, `gtfs_pipeline.py refresh-static --force`
(with `--agency-id <id>` for one agency, or without it for every configured
one) reloads it the same way. That is how a column the loader derives, such
as one a migration adds to the static tables, reaches a timetable whose
source has not changed. The page's buttons have no such option.

Tables read: `agencies`, `agg_meta`, `agg_route_daily`, `pipeline_runs`,
`rt_field_coverage_probes`, `agg_feed_health`, `static_trips`,
`static_calendar`, `static_calendar_dates`, `agg_static_version_summary`,
`agency_weather_stations`, `route_performance_standards`,
`ridership_weights`. The two editors write `route_performance_standards`
and `ridership_weights`; every write additionally records an
`admin_audit` entry.

### RT field-coverage verdicts

The drawer's RT field diagnostics show the agency's verdicts in
`rt_field_coverage_probes`. Service delivered, dwell and running time,
schedule-realism padding and headway quality read their RT-optional fields
only for an agency with a complete, unexpired, confirmed verdict, and an
expired verdict reads like a missing one. Three paths write a verdict, all
through `record_field_coverage_probe` with the same thresholds:

- the drawer's probe button (`POST .../probe`);
- `scripts/probe_rt_field_coverage.py --record`, run by hand;
- the daily renewal in `pipeline/rt_coverage_reprobe.py`.

The renewal probes every active `static_join` agency whose verdict is
missing, partial, refuted, expired, or within 14 days of expiring. It runs as
the last pass of an unscoped `POST /internal/cron/ingest` sweep (after the
lock is released, like the weather pass) and as `gtfs_pipeline.py
reprobe_rt_coverage` (`make reprobe-rt-coverage`). It only probes inside each
agency's local service window (07:00–20:00), and it records nothing from a
poll with fewer than 20 stop_time_updates: a poll that small often carries
arrivals on half its updates, which would read as refuting `arr_delay`. A
verdict it writes is trusted for 180 days. When nothing is due or the window
is closed it does nothing and writes no run row, so a deployment needs one
trigger in service hours. Late morning suits every agency's feed.

Each agency it probes gets a `probe` row in `pipeline_runs`, drawn as its own
lane on the board. The row closes `ok` when a verdict was written, `skipped`
when the poll was too thin or the agency's feed changed mid-probe, and `error`
when the feed could not be fetched or decoded. The rows count is the poll's
stop_time_updates. One agency's failure never stops the next agency or the
sweep.

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
- `POST /api/admin/invites` — invite a new user. The invite applies when
  sign-in creates the account, so an email that already has one is refused
  with 409 `account_exists`.

Tables read/written: `users`, `sessions` (revoked on suspend and delete,
and one at a time), `oauth_identities`, `user_llm_keys` (read-only,
for the drawer's provider indicator), `api_keys`, `user_invites`. Every
mutation records an `admin_audit` entry.

`DELETE /api/admin/users/{uid}` is a soft-delete. It anonymizes the row,
sets `users.deleted_at`, revokes the user's API keys and replaces the owner
address recorded on them. A deleted user is terminal. A single or bulk PATCH
that includes one, a second DELETE, or a key issued to one each get a 409.
The list and drawer label the row Deleted and offer no Resume control.

The drawer's "Usage (last 30 days)" section reads
`GET /api/admin/users/{uid}/activity?days=30` (`days` 1–365): per route
template and agency, the requests and errors summed from
`user_activity_daily`. The API counts every authenticated `/api/*` request
into that table (`api/activity.py`, flushed every 30 s), attributing
API-key requests to the key's owner. It stores route templates and days
only — never raw paths, query text, or finer timestamps.

## Ops

Route: `/admin/ops`, backed by `AdminOpsPage`.

Endpoint: `GET /api/admin/ops` — a read-only health snapshot with two
sub-checks, each degrading on its own (the response is still 200, and the
page shows that check as unknown rather than as healthy):
- migration status (`pipeline.health.migration_status`): the on-disk
  migration files against `schema_migrations`, as the latest applied
  version and how many are pending;
- aggregate freshness (`pipeline.health.aggregate_freshness`, read live —
  the board's staleness alerts come from the same check): per active agency,
  its last analyze time (`agg_meta`), the newest completed day in ClickHouse
  `updates`, how many days its newest `agg_route_daily` date trails that day
  and whether that makes it stale, and its recent clamp rate
  (`agg_feed_health`).

The page shows a status strip for the two checks and a per-agency table
(agency, last analyzed, freshness, data through, implausible %).

## Architecture

Route: `/admin/architecture`, backed by `AdminArchitecturePage`.

The page shows a Mermaid data-flow diagram of `AGENTS.md`'s "Architecture
pointers", kept in sync with that section by hand (nothing derives it), and
an index of `docs/features/*.md` with the selected doc rendered beside it.

Endpoints:
- `GET /api/admin/architecture/docs` — every `docs/features/*.md` file's slug
  and `# Title`, sorted by filename; files holding only HTML comments or
  whitespace are left out.
- `GET /api/admin/architecture/docs/{slug}` — one doc's raw Markdown; a
  malformed slug, one that would resolve outside that directory, a missing
  file and a comment-only file each answer 404.

Both read the filesystem on every request and touch no database.

## Audit log

Route: `/admin/audit`, backed by `AdminAuditPage`.

Endpoint: `GET /api/admin/audit` — a single cursor-paged, filterable
timeline merging `admin_audit` (every admin mutation across this whole
surface, written through `api.admin_audit.record_admin_action`) with the
login/login-failure rows of `login_events` (mapped to `login.ok`/
`login.fail`). Admin mutations are recorded directly into `admin_audit`; the
other `login_events` kinds (a user's own logout, account creation and invite
use, and a role granted by seeding or `ADMIN_EMAILS`) are not part of this
timeline.

## Feature flags

Route: `/admin/flags`, backed by `AdminFlagsPage`.

Endpoints:
- `GET /api/admin/flags` — every registered flag, resolved (value, source,
  env default, override provenance).
- `PATCH /api/admin/flags/{key}` — set or replace this flag's DB override;
  `reason` is mandatory.
- `DELETE /api/admin/flags/{key}` — clear this flag's DB override, returning
  it to its env resolution (the page's way back to the deployment's own
  setting). Idempotent: a flag with no override row succeeds unchanged. An
  unregistered key answers 404 on both writes.

Tables: reads and writes `feature_flags` (one row per overridden key,
upserted on PATCH and deleted on clear — no history, only the latest
reason/actor/timestamp per key). Every PATCH records an `admin_audit` entry
(`flag.set`), and a clear that removed a row records `flag.cleared`. Both
invalidate the flag cache immediately. See `pipeline/flags.py` for the
registry, precedence, and cache TTL, and `README.md`'s kill-switch section
for the full key list.

`login_required` is the sign-in gate (`api/middleware/login_gate.py`): on,
every `/api/*` route except the sign-in flow and `/api/config` refuses a
signed-out caller while SSO is configured. Turning it off reopens the app to
signed-out visitors; it is the recovery switch when OAuth is down (see
`docs/deploy-railway.md`).

## Ask ops

Route: `/admin/ask`, backed by `AdminAskOpsPage`.

Endpoints:
- `GET /api/admin/ask/queries` — paged query log.
- `GET /api/admin/ask/funnel` — stage/success counts.
- `POST /api/admin/ask/promote` — promote one Stage-3 ("rag") query's
  cached intent into the RAG index ahead of the scheduled batch job's own
  hit-count/quiet-days gate. The suggestion it publishes is that query's own
  question text, the one the admin reviewed.
- `GET /api/admin/ask/eval` — latest local eval artifact, or `null` when
  nothing has produced one yet.

Tables: reads `ask_query_log`; `promote` additionally reads/writes
`ask_intent_cache` and `rag_chunks` (via `pipeline.query.intent_promotion`)
and records an `admin_audit` entry.
