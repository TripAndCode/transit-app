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
  app shell, and `GET /api/snapshots/{id}` and `GET /api/snapshots/{id}/today`
  are public even while `login_required` is on. The id is 128 random bits
  (`secrets.token_urlsafe(16)`), and nothing lists ids to a non-admin. A
  malformed, unknown or deleted id gets the same 404 from both.
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
Route rows are not links, since a route's live page would show today's
figures.

Only the screen's own report is frozen. The panels the Routes screen shows
beside its table (headway quality, the agency's performance standards) come
from endpoints of their own and are not part of a snapshot.

## Compared with today

The page asks the API for the snapshot alone until the reader turns on
「今日の数値と比較」 / "Compare with today's figures" (off on every visit).
Then it asks `GET /api/snapshots/{id}/today` once, and marks the stored
figures against today's; the stored figures themselves never change.

- **Like for like.** The endpoint replays the snapshot's stored `params` (the
  resolved dates, never a rolling window, the scope and the screen's options)
  through the same function the create used, `render_report`, in the reader's
  language. A stored query the current code refuses (a preset or sort since
  retired, a scope value it no longer takes), or a screen with no computation,
  answers `{"report": null, "unavailable": "not_computable"}`; an agency no
  longer listed answers `"agency_removed"`. The page says either calmly:
  today's figures are unavailable, and why.
- **What is compared.** What the page prints, never prose: each figure at
  the precision it is printed, so two values that print the same are not a
  difference, and only what the snapshot's own screen shows
  (`frontend/src/components/snapshotCompare.ts`). An early-departure rate a
  snapshot never showed (its definition names no threshold) is not compared.
  - the Routes table's figure columns, rows matched by route. A moved
    figure shows today's value beside the stored one (「今日 15.0%」 /
    "today 15.0%"); a route only one side lists is marked
    「スナップショットのみ」 / "only in the snapshot" or 「今日のみ」 / "only
    today", today's routes following the stored ones. Beneath, the page says
    plainly that such a route is in one side's list alone. Only when a side
    reached its row limit (it listed fewer routes than qualified) does it
    add that the route may have moved past that limit rather than left the
    data.
  - the council report's headline tiles, from its structured fields
    (`frontend/src/components/analysis/councilTiles.ts`, the one table both
    the tiles and the comparison read): the on-time, early-departure and
    service-delivered rates, the average delay, the observations, the
    planned trips with the days a timetable plans, the reason a delivered
    rate is missing, and the agencies board's verdict with every figure its
    reason prints (coverage, the on-time interval, the implausible-reading
    share, each against its threshold). A note's values count only where
    the note is printed. The prose is written from those fields and is not
    compared.
  - Equal figures show nothing extra; a figure missing on one side counts as
    moved.
- **Both sides named.** Above the figures, each side's method version, build
  time (JST, or that it was a live scan) and on-time definition. When the
  method versions differ the page says so plainly ("Today's figures use
  method X; the snapshot used Y"), and when the on-time windows differ (a
  snapshot that left its window to the default, after the default changed)
  it says so too: a difference may then come from the method rather than the
  data. A count of the differences, or that every figure matches, follows.
- **Cost.** The endpoint computes on a public path, so every caller is held
  to `SNAPSHOT_TODAY_LIMIT` (`api/middleware/ratelimit.py`, 10 a minute),
  stricter than the snapshot read's per-tier budget (60 a minute on the free
  tier). A repeated query reuses the compute caches behind it: the Routes
  table's computation and the agencies board behind the council verdict are
  each cached for five minutes. The council report's pooled on-time figures
  are read from the aggregates each time, or scanned live for a scope the
  aggregates cannot serve; the limit is what bounds those. The page reuses
  one answer for five minutes and asks again on a language switch, since the
  council report's response is written in the reader's language. A caller
  over the limit gets a calm note to wait, with no retry button, since a
  retry would meet the limit again.
