# Frozen snapshots

A snapshot stores the figures a screen showed, with their method stamp, so its
link reproduces them exactly after any later re-analyze. The share link in the
Export menu is only the current URL: opened after a rebuild, it shows the new
figures. A snapshot is what council material quotes when a number has to stay
the number that was handed out.

## Who does what

- **Admins** take snapshots. On the council report (Reports, `doc=council`)
  and the Routes table, an admin sees an Export menu whose "Create snapshot"
  item (「スナップショットを作成」) stores the screen as it is asked for (scope,
  on-time window, sort, order, sparse and left-out toggles, row count) and
  copies the snapshot's link, `/s/{id}`. Everyone else sees no change on those
  screens.
- **Anyone holding the link** reads it, signed in or not: the page
  (`frontend/src/pages/SnapshotPage.tsx`) sits outside the sign-in wall and the
  app shell, and `GET /api/snapshots/{id}` is public even while
  `login_required` is on. The id is 128 random bits
  (`secrets.token_urlsafe(16)`), and nothing lists ids to a non-admin. A
  malformed, unknown or deleted id gets the same 404.
- **Admins** list and delete them through `/api/admin/snapshots`
  ([admin-control-room.md](admin-control-room.md#snapshots)). Each create and
  delete is in the audit log.

## What is stored

One `snapshots` row (`db/migrations/0092_snapshots.up.sql`):

- `screen`: `council_report` or `route_table`. The table also allows
  `route_page`, which cannot be created yet: the route page fetches its data
  inside its own components, so there is nothing yet to render it from stored
  responses.
- `params`: the resolved scope, with explicit dates even when the request
  left them to the default window, and the screen's own options.
- `payloads`:
  - `report`: the response the screen's endpoint
    (`GET /api/{agency_id}/reports/{council_summary|route_table}`) serves,
    once per language, each with its `definition` (`DefinitionMeta`: method
    version, build time, timetables, ingest sources). Both languages are
    stored because the server writes the council report's prose. Everything
    but that prose (`text`) and the serving time (`rendered_at`) must match
    between them, or the create answers 409 and stores nothing.
  - `routes`: the agency's route catalogue (`GET /api/{agency_id}/routes`), so
    the rows keep the labels they had.
  - `credit`: the open-data credit for the agency's feed, per language.
- `method_version`, `agency_name`, `created_at`, `created_by` and an optional
  `title` (up to 200 characters) and `note` (up to 2000).

The server computes every payload itself, through the same function the
screen's endpoint runs (`api.routers.reports.render_report`); a create request
names only the screen, the agency and the query, and any other field is
refused. Payloads over 1 MiB serialized are refused with 413.

## The page

`/s/{id}` draws the stored responses with the live screens' own components
(`CouncilSummaryBlock`, `ReportTable`, `DefinitionMetaBlock`), under a banner
「スナップショット（2026-11-09 14:05 JST 作成、手法 2026.11.0）」 / "Snapshot
(created 2026-11-09 14:05 JST, method 2026.11.0)". Beneath it the scope reads
as the live scope sentence does, every condition the figures were computed
under, one the screen's data did not use struck through; a Routes table also
states its observation floor and the routes it left out, as disabled toggles.
It asks the API for the snapshot alone, so nothing on it can drift to today's
figures; route rows are not links, since a route's live page would show
today's.

Only the screen's own report is frozen. The panels the Routes screen shows
beside its table (headway quality, the agency's performance standards) come
from endpoints of their own and are not part of a snapshot.
