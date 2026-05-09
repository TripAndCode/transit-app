# Transit App — portfolio uplift (2026-05-09)

## Goal

Lift the existing GTFS-RT delay app from "competent internal admin tool" to "portfolio piece that signals senior-engineer breadth" for two audiences:

1. **AI / LLM-product engineers** — Anthropic, OpenAI, AI-native startups
2. **Generalist senior / staff engineers** — full-stack with depth across data pipeline, geo, LLM tool-use, deploy

Domain (Japanese transit, GTFS-JP, kanji UI) is a bonus — surfaced in the README, not the headline.

## Non-goals

- Monetization / billing tier work — already covered in `2026-05-04-monetization-design.md`
- Hiroshima 3-operator onboarding — feasibility documented in `specs/2026-05-09-hiroshima-feasibility.md`; pipeline change tracked there
- Alerts (`alerts.bin`) ingestion — deferred per Hiroshima feasibility doc
- Calendar / service-day enforcement — invisible to portfolio viewer
- Sidebar redesign, "Now" header pill, empty-state illustrations — low signal vs cost

## Phasing

P0 (Linode deploy baseline) is treated as merged before P1 starts. P1 → P5 sit on top of a live Linode prod box.

| Phase | Headline | Mergeable | Demo-able |
|---|---|---|---|
| P0 | `linode-deploy` branch merged, domain live, Caddy auto-LE | yes | yes |
| P1 | GTFS shapes loader + identity + opacity-bug fix | yes | partial |
| P2 | Custom vector basemap + halo/heatmap layers + cluster + route-line color | yes | yes |
| P3 | Live Ask chips + tool-use trace inspector + golden-set eval CI | yes | yes |
| P4 | Camera fly-along + sparklines + warm dark mode + percentile/by-hour map modes | yes | yes |
| P4.5 | `/admin/ops` surface, heartbeat monitor, pg_dump cron, Caddy log summary | yes | yes |
| P5 | README rewrite + ARCHITECTURE.md + 2 demo GIFs | yes | n/a |

---

## P0 — Linode baseline

Merged on `origin/main` as PR #6, commit `6712e6f` (chore(deploy): switch from Fly.io to Linode VPS via compose prod profile). Provides:

- Single 2 GB Linode (Tokyo or Osaka), ~$12/mo
- `docker compose --profile prod` with `app`, `caddy`, `db` (PostGIS 14 + pgvector)
- Caddy reverse proxy, automatic Let's Encrypt on domain swap
- GitHub Actions hourly ingest cron hitting `/internal/cron/ingest` with the `X-Cron-Secret` header (plain shared-secret match against `CRON_SECRET` env)
- `.env` model: `CADDY_SITE_ADDRESS`, `POSTGRES_PASSWORD`, `CRON_SECRET`, `GROQ_API_KEY`
- Hardening checklist in `docs/deploy-linode.md`: ufw, deploy user, no-root SSH

**Outstanding before P1 demo-readiness (not blockers for P1 dev):**
- Box provisioned + first boot
- Domain pointed, HTTPS green via Caddy auto-LE
- Hourly cron verified writing rows on the box
- pg_dump + heartbeat (P4.5d, P4.5c) — can land alongside P4.5

P1 development can proceed locally; deploy hardening lands on its own track.

---

## P1 — Foundation

Goal: stop the map from looking fake; give the app a wordmark voice.

### P1a. GTFS shapes loader

Real road geometry replaces straight stop-to-stop polylines on the route overlay. Audit identified this as the single biggest visual lift — basemap swap multiplies on top of it.

**Schema:**

```sql
CREATE TABLE static_shapes (
  agency_id INT NOT NULL REFERENCES agencies(id),
  shape_id  TEXT NOT NULL,
  geom      geometry(LineString, 4326) NOT NULL,
  PRIMARY KEY (agency_id, shape_id)
);
CREATE INDEX static_shapes_gix ON static_shapes USING GIST (geom);
```

**Loader:** extend the existing GTFS-static loader to read `shapes.txt`, group by `shape_id`, order by `shape_pt_sequence`, build `LineString` via `ST_MakeLine(ST_MakePoint(lon,lat) ORDER BY seq)`. Idempotent upsert keyed on `(agency_id, shape_id)`.

**API:** the existing route-shape endpoint joins `static_trips.shape_id → static_shapes.geom`. Response shape unchanged from the frontend's perspective — same GeoJSON, real geometry inside.

**Multi-shape route handling:** when multiple trips of one route reference different shape_ids, return the most-frequent shape per `(route_id, direction_id)`. Document this choice in code; fancier logic deferred.

**Fallback:** agency without `shapes.txt` (rare but possible) → loader logs `WARN no shapes.txt for agency_id=X`, endpoint falls back to current stop-to-stop polyline. UI no-op.

### P1b. Identity

- **Name:** keep `遅延ダッシュボード`. Add subtitle treatment: `リアルタイム × 時刻表` (realtime × timetable).
- **Wordmark:** Noto Serif JP for the headline (`<h1>` and tab title), Noto Sans JP for body. Add `<link>` to `index.html`; the existing CSS-var system absorbs the swap.
- **Severity dot move on Live cards:** replace `<Stat color={delayColor(...)}>+12分34秒</Stat>` with leading severity dot (`●`) plus neutral-color digit. Tooltip retains full precision.
- **Round delay to 分** on Live card display; full precision on hover only.
- **Sidebar emoji glyphs → Lucide line icons.** Add `lucide-react` if not already present.

### P1c. Quick fixes shipped with P1

- Opacity floor by severity in heatmap layer (do not bury severe stops with low samples).
- Filter `samples = 1` from heatmap source by default; legend toggle re-enables.

### P1 tests

- `tests/test_static_loader.py` — fixture `shapes.txt` with three `shape_id`s, assert PostGIS geom built and idempotent re-run.
- `tests/test_route_geometry.py` — endpoint returns LineString from joined shapes when present; falls back to stop-polyline when absent.
- Frontend: snapshot test on Live card markup confirming severity dot present and digit unstyled.

---

## P2 — Map wow

### P2a. Custom vector basemap

Build a Mapbox style (Mapbox Studio fork or hand-edited `style.json`) with the existing palette:

- Land `#fafaf8` (cream)
- Roads `#e8e0d0` (sand)
- Water + parks `#c8d4ca` (sage)
- Faint admin lines, POI labels hidden at z<14

Env: `VITE_MAPBOX_TOKEN`, `VITE_MAP_STYLE_URL`.

**Fallback chain:** missing token → Carto Positron (no-token vector) so the demo never blanks for a code-reader running locally.

Validate via the `mapbox-style-quality` skill before shipping.

### P2b. Layered halo + crisp dots

Source: existing heatmap GeoJSON (no API change needed).

- **Layer A (halo):** `circle-blur: 0.6`, radius interpolated by `samples` (6→18px), opacity `0.25`, severity color
- **Layer B (dot):** 4px radius, 0.5px white stroke, severity color, opacity floored by severity (P1c fix)
- **Layer C (z<10):** native `heatmap` type for density read-out

Hover via `feature-state` → grow + outline. No pulses on regular dots; slow 2s opacity pulse only on stops with avg delay >10 min (calm-UI rule).

### P2c. Low-zoom cluster

`cluster: true, clusterRadius: 40` on heatmap source. Cluster color is the avg severity of children stops.

### P2d. Route line color-coded by delay (v1)

Single-color polyline whose hue maps from the route's current avg delay severity. Geometry from `static_shapes` (P1a); flat fallback if shape missing.

**Stretch v2 (deferred to P4 if scope holds):** per-segment `line-gradient` from a new aggregate `agg_route_segment(agency_id, route_id, segment_seq, p50_delay_min)`. Skip until v1 lands.

### P2 tests

- Unit: basemap URL builder honors token/no-token paths
- Unit: severity color ramp produces expected stops at boundary inputs
- Snapshot: layer config object shape stable
- Manual e2e checklist: vector tiles render, halo+dot layered correctly, cluster collapses at z<10

### P2 failure modes

- Token missing → Carto fallback, console.info (no UI noise)
- `static_shapes` empty → flat stop-polyline line (P1a fallback)
- Tile rate-limit → console.warn only

---

## P3 — Ask wow (centerpiece for the AI/LLM-eng audience)

### P3a. Live-data chips

Replace the three hardcoded chips with a generator endpoint:

```
GET /agencies/:id/ask/suggestions
→ { chips: [ {label, query}, ... ] }
```

Examples derived from current rows: `"系統5が今+8分 — 原因は?"` / `"本日ワースト3"` / `"AMピーク vs 今"`.

Re-fetch on tab mount + every 60s. Empty / error fallback: the three current hardcoded chips.

**Signal:** product instinct — UX shaped by data, not lorem ipsum.

### P3b. Tool-use trace inspector

Backend: the `/ask` response gains a `trace` array.

```ts
type TraceStep = {
  tool: string;            // e.g. "query_routes_by_delay"
  args: Record<string, unknown>;
  sql: string;             // exact SQL fired
  rows_returned: number;
  latency_ms: number;
  sample_rows?: unknown[]; // first 5 rows, lazy-loaded on expand
};

type AskResponse = {
  answer: string;
  trace: TraceStep[];      // capped at 8 items, "...truncated" marker if more
};
```

Frontend: collapsible panel "ツール実行トレース" under the answer (default collapsed). Each step row shows tool name + arg pills + `latency_ms` + row count. Click a row → expand SQL block (mono, copy-to-clipboard) plus a sample-rows table.

**Signal:** every recruiter who opens this sees real LLM-eng work — tool routing, SQL, latencies. Most portfolio chat demos hide this; this exposes it.

### P3c. Golden-set eval suite (CI)

File: `tests/eval/golden_set.yaml` with 12–15 Q&A pairs:

```yaml
- q: 今日の遅延ランキングは?
  expected_tools: [query_routes_by_delay]
  expected_columns: [route_name, avg_delay_min]
  forbidden_phrases: ["I cannot", "申し訳"]
```

Runner: `tests/eval/run_eval.py` with two modes:

- **replay** (CI default): replay recorded Groq responses, fast/free/deterministic
- **live** (`EVAL_MODE=live`): real Groq calls; refresh recordings

Pass threshold 80%. GitHub Action posts pass-rate as a PR comment. Allowed-failure on Groq outage so eval flake doesn't block merges.

**Signal:** rare in portfolio projects; shows LLM-regression discipline.

### P3d. Model toggle (optional, ship if scope allows)

Header dropdown: Groq llama-3.3-70b (default), Groq llama-3.1-8b (fast), optional Anthropic claude-haiku-4-5. Persists in `localStorage`. Backend `?model=` param. Tool-use protocol unchanged across providers.

**Skip if** P3a + P3b + P3c eat all available time.

### P3 tests

- Backend unit: suggestions endpoint deterministic from fixture rows
- Backend unit: trace serialization preserves order; size cap at 8 + truncated marker
- Frontend: trace panel snapshot (collapsed default; expanded reveals SQL block)
- Eval: see P3c

### P3 failure modes

- Suggestions slow → use last-good cache; do not block tab render (skeleton, no spinner — calm)
- Trace too large → cap 8 + truncated marker
- Eval CI flake → warning, not failure
- Provider outage on toggle → fall back to Groq default + inline banner (calm-UI rule)

---

## P4 — Delight + tail

### P4a. Camera fly-along

On route polyline click: 3-second `flyTo` sequence stop-to-stop along the `static_shapes` LineString, with a brief stop-name tooltip per stop. Skippable (Esc / click anywhere). Driver: `requestAnimationFrame` queue with cubic-bezier easing.

The "one delight gesture" — memorable, shareable, primary demo-GIF candidate.

### P4b. Sparkline per Live card

60×16 hand-rolled SVG polyline showing the last 24h hourly p50 from existing `agg_route_hour`. No charting lib (signal: small SVG instinct, no bundle bloat). Edge cases: all-zero rows → flat dashed line; missing hours → dotted segment.

### P4c. Warm sepia dark mode

Token flip: `--bg #1a1a18`, `--text #e8e4d8`, severity-ramp hue cooled by ~5°. Toggle in the existing `SettingsDrawer`; persist in `localStorage`. Mapbox dark variant via parallel `style-dark.json` (Mapbox Studio fork) — runtime swap on theme change.

### P4d. Percentile / by-hour map modes

Mode dropdown above the map:

- `現在の遅延` (default avg)
- `p90 ワースト`
- `AMピーク (07-09時)`
- `夕ピーク (17-19時)`

Backend: heatmap endpoint gains `?mode=` →

- `現在の遅延` (default): unchanged, current avg from `agg_route_hour`
- `p90 ワースト`: same aggregate, swap `avg_delay_min` for `p90_delay_min` in projection. If `p90` column does not yet exist on `agg_route_hour`, add it via the same migration that lands this phase (cheap; computed in the existing rollup job).
- `AMピーク (07-09時)` / `夕ピーク (17-19時)`: filter `agg_route_hour.hour IN (7,8,9)` or `(17,18,19)` and re-aggregate to per-stop avg.

Frontend re-fetches the source on mode change; layer config unchanged.

### P4 tests

- Fly-along: unit on keyframe generator (shape + stops → camera path); manual visual
- Sparkline: snapshot with 24-row, all-zero, single-point fixtures
- Dark mode: theme-token application snapshot
- Mode endpoint: param routing returns the correct aggregate

---

## P4.5 — Ops surface + observability

Demonstrates self-hosted prod ownership. Cheap to build, rare in portfolio projects.

### P4.5a. `/internal/status` endpoint

Distinct from the existing public `/health` (used by Caddy/compose healthchecks — stays minimal, no auth). `/internal/status` is gated by the existing admin auth and returns:

- `last_cron_run_at`, `last_ingest_rows_per_agency`, `total_rows_per_agency`
- `disk_free_pct` (read from `/proc` via `shutil.disk_usage` or `psutil`)
- `db_uptime_seconds`, `db_connection_count`

### P4.5b. `/admin/ops` page

Tiny React route gated by existing admin auth. Shows:

- Last cron run + age (sage if <2h, sand if older)
- Per-agency: last ingest time, rows ingested, total rows
- Host disk %, DB connection count, DB uptime
- Caddy access log tail summary: top routes last hour, 4xx/5xx counts

### P4.5c. Heartbeat monitor

The hourly cron also POSTs to a Better Stack (or Healthchecks.io) heartbeat URL on success. README badge shows live/down.

### P4.5d. pg_dump nightly

Host crontab (not in container):

```cron
0 17 * * * cd /home/deploy/transit-app && docker compose exec -T db pg_dump -U transit transit | gzip > /home/deploy/backups/transit-$(date +\%F).sql.gz
0 18 * * * find /home/deploy/backups -mtime +7 -delete
```

Document in `docs/deploy-linode.md` and `ARCHITECTURE.md`.

### P4.5e. Caddy access log summary

Tail-and-count summary on the Ops page. Read latest 1 MB of `caddy_access.log`, group by route prefix, count statuses. No analytics service.

### P4.5 tests

- `/internal/status` unit: deterministic shape from fixture system metrics
- Ops page: snapshot
- Heartbeat: unit on POST formation; failure path is silent (cron success not blocked)

### P4.5 failure modes

- Heartbeat endpoint down → cron continues, console.warn only
- pg_dump full disk → document a recovery runbook in `docs/deploy-linode.md`
- Caddy log file rotated mid-read → ignore, retry next request

---

## P5 — Story

### P5a. README rewrite (top half)

- "What this is" — 2 sentences + 2 demo GIFs (map fly-along, Ask trace inspector)
- Live demo URL (custom domain via Caddy auto-LE)
- Mermaid architecture diagram: `agencies.csv` → ingest pipeline → Postgres+PostGIS → FastAPI → React, with Groq tool-use sidecar + GTFS static loader path + GH Actions cron + Better Stack heartbeat
- Tech stack table: Python 3.12 / FastAPI / Postgres+PostGIS+pgvector / MapLibre / Mapbox style / Groq tool-use / React+Vite / Pytest / Caddy / Linode
- **Engineering decisions** section, ~5 bullets with rationale:
  - Oracle archive ingestion path (vs live HTTP fetch) — why
  - PostGIS + GTFS shapes for real road geometry
  - Custom Mapbox vector style (over OSM raster)
  - Golden-set eval as LLM regression discipline
  - Caddy auto-LE on Linode (over PaaS) — full Postgres+PostGIS+pgvector control, JP-region residency, ~$12/mo, learning surface
- Existing setup steps move below the fold

### P5b. ARCHITECTURE.md (new file)

- GTFS-RT proto → ingest → `updates` table → aggregates flow
- Per-subsystem component diagrams (Mermaid)
- Schema snapshot (auto-generated from migrations or hand-curated)
- LLM tool-use chain + eval harness section
- **Deployment** section: VPS choice rationale, Caddy as TLS proxy, compose `--profile prod`, cron shared-secret header pattern (`X-Cron-Secret` against `CRON_SECRET` env, constant-time compare), backup script, hardening checklist (ufw / no-root SSH / deploy user)

### P5c. Demo GIFs

- `docs/media/demo-map.gif` — load page → halo dots → click route → fly-along (≤8s, ≤2 MB)
- `docs/media/demo-ask.gif` — type question → stream answer → expand trace → SQL revealed (≤8s, ≤2 MB)

Capture via Kap or `vhs`. Reference from README and this spec.

### P5 tests

- README link-check via `lychee` or `markdown-link-check` in CI
- Mermaid render validated locally before commit
- ARCHITECTURE.md spot-checked on PR review (no automated test)

---

## Schema-change summary

New table:

- `static_shapes(agency_id, shape_id, geom)` — P1a

Optional / deferred:

- `agg_route_segment(agency_id, route_id, segment_seq, p50_delay_min)` — P2d v2 stretch only

No breaking changes to existing tables.

## API-change summary

Modified:

- Route-shape endpoint: response unchanged shape, geometry now from joined `static_shapes` when present, fallback otherwise
- `/ask` response: gains optional `trace: TraceStep[]` field; clients that ignore it keep working
- Heatmap endpoint: gains optional `?mode=` param; default behaviour unchanged when absent

New:

- `GET /agencies/:id/ask/suggestions` — chips
- `GET /internal/status` — ops JSON (admin-auth-gated)
- `GET /admin/ops` — SPA route serving the ops page

No removals.

## Risks

- **Mapbox token burn:** custom style on free tier (50k loads/mo). Adequate for portfolio; document fallback to Carto if exceeded.
- **Groq rate limits during eval:** mitigated by replay mode being CI default.
- **Single-box prod:** documented deliberately as a portfolio choice. Document the upgrade path (Linode 4 GB or split DB) in ARCHITECTURE.md so reviewers see the reasoning.
- **Demo data reality:** seed is synthetic, ~550 obs/route. Pre-launch query `SELECT agency_id, COUNT(*), MIN(captured_at), MAX(captured_at) FROM updates GROUP BY 1` to confirm real coverage before recording demo GIFs.
- **JP-only UI for international viewers:** README in English with screenshots + tooltips bridges the gap; do not bilingual the UI itself (would dilute calm-UI palette).

## Open questions (non-blocking)

- Mapbox Studio vs hand-edited `style.json` — defer until P2 starts; pick whichever lands faster
- Better Stack vs Healthchecks.io for heartbeat — both have free tiers; pick when P4.5 starts
- Whether to ship model toggle (P3d) — decide based on remaining bandwidth after P3a–c land

## Out of scope, recorded for later

- Hiroshima 3-operator pipeline cut-over (separate spec)
- Monetization tier UI (separate spec)
- Per-segment route-line gradient (P2d v2 stretch)
- Calendar / service-day enforcement
- Sidebar redesign, "Now" header pill
