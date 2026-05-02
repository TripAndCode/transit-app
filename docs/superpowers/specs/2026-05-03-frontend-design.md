# Frontend Design Spec

**Date:** 2026-05-03
**Status:** Approved (brainstorm complete, awaiting implementation plan)

## Overview

Add a single-page web frontend to the Transit Delay App (`/Users/yofurusawa/transit-app`). The frontend exposes the existing FastAPI endpoints to browser users via four tabs: Map (default), Ask, Live, Reports. Bundled into the existing Railway deployment as a single service via a multistage Dockerfile.

## Goals

- Wire up the existing API surface (`/agencies`, `/api/{id}/ask|delays|reports|routes|stops`) in a usable browser UI
- Single Railway service deploy (one URL, no CORS issues in prod)
- Calm, low-stress UI: muted palette, generous whitespace, no alarms
- Japanese chrome (matches GTFS-JP user base)
- Read-only by default; hidden admin form for agency creation behind `?admin=1`
- Extensible: layout/data-flow holds up if more tabs/agencies added later

## Non-goals

- Authentication/login UI (backend `POST /agencies` has no auth — admin form is dev-only)
- Server-side rendering, i18n framework
- Automated frontend tests in v1 (manual smoke + TS strict)
- Mobile-first design (mobile is "usable", not optimized)
- WebSocket chat (`/api/{id}/chat` exists but out of scope; REST `/ask` only)

## User & UX decisions

| Topic | Decision |
|---|---|
| Audience | Generic — keep clean & functional, no audience-specific polish |
| Chrome language | Japanese only (English in code/comments) |
| Mobile | Desktop primary; sidebar collapses to drawer at `<768px` |
| Layout | Left sidebar (4 nav items) + top header (agency picker, settings) |
| Default tab | Map — visual, single GeoJSON fetch, immediate "what does this app do" signal |
| Write operations | Read-only UI; admin agency form hidden behind `?admin=1` |
| Map tiles | Configurable via `VITE_MAP_STYLE_URL`; default = OSM raster style JSON |
| Agency picker | Built as searchable combobox (handles 10+); shows static label when only 1 agency |

## Tech stack

| Layer | Choice |
|---|---|
| Framework | React 18 + TypeScript + Vite 5 |
| Routing | `react-router-dom` v6 — URL-addressable tabs (`/agencies/:id/{map\|ask\|live\|reports}`) |
| Data fetching | `@tanstack/react-query` v5 — caching, polling for Live tab, mutation for Ask |
| Map | `maplibre-gl` v4 |
| Styling | Plain CSS modules (one `.module.css` per component); no Tailwind |
| Bundler | Vite |
| Node | 20 (build stage) |

## File structure

```
frontend/
├── package.json
├── vite.config.ts
├── tsconfig.json, tsconfig.node.json
├── index.html
├── .gitignore                 # node_modules, dist, .vite
├── public/                    # favicon
└── src/
    ├── main.tsx               # bootstrap: QueryClientProvider, BrowserRouter
    ├── App.tsx                # shell: <Header/> + <Sidebar/> + <Outlet/>
    ├── styles/
    │   ├── global.css         # tokens (colors, spacing), reset
    │   └── tokens.ts          # color/spacing constants exported for inline use (e.g. map ramp)
    ├── api/
    │   ├── client.ts          # fetch wrapper, ApiError class
    │   ├── types.ts           # Agency, Intent, Report, LiveDelay, HeatmapFeature
    │   └── hooks.ts           # useAgencies, useReports, useReport, useLiveDelays, useHeatmap, useAsk
    ├── components/
    │   ├── Header.tsx
    │   ├── Sidebar.tsx
    │   ├── AgencyPicker.tsx          # searchable combobox
    │   ├── SettingsDrawer.tsx        # API key field
    │   ├── EmptyState.tsx
    │   ├── ErrorBanner.tsx
    │   └── Skeleton.tsx
    ├── tabs/
    │   ├── MapTab.tsx
    │   ├── AskTab.tsx
    │   ├── LiveTab.tsx
    │   └── ReportsTab.tsx
    └── admin/
        └── AgencyForm.tsx     # mounted only when `?admin=1`
```

## Routing

| Path | Component |
|---|---|
| `/` | redirect → `/agencies/{firstAgencyId}/map` |
| `/agencies/:id` | redirect → `/agencies/:id/map` |
| `/agencies/:id/map` | `<MapTab/>` |
| `/agencies/:id/ask` | `<AskTab/>` |
| `/agencies/:id/live` | `<LiveTab/>` |
| `/agencies/:id/reports` | `<ReportsTab/>` (no report selected) |
| `/agencies/:id/reports/:type` | `<ReportsTab/>` (specific report selected) |
| `*` | 404 in browser (FastAPI SPA fallback returns `index.html`; React-Router renders 404 component) |

`?admin=1` (any path) mounts the admin floating button.

## API client

Single fetch wrapper, base URL from env:

```ts
const BASE = import.meta.env.VITE_API_BASE_URL ?? "";  // "" = same-origin (prod)
async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const apiKey = localStorage.getItem("api_key");
  const r = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(apiKey ? { "X-API-Key": apiKey } : {}),
      ...init?.headers,
    },
  });
  if (!r.ok) throw new ApiError(r.status, await r.text());
  return r.json();
}
```

`ApiError extends Error` with `.status: number` and `.body: string`. Tabs read `.status` to render contextual messages (429 → rate limit, 5xx → server error, network → connection error).

### React Query hooks

| Hook | Key | Endpoint | Refetch |
|---|---|---|---|
| `useAgencies()` | `["agencies"]` | `GET /agencies` | on mount, staleTime 5min |
| `useReports(id)` | `["reports", id]` | `GET /api/{id}/reports` | on mount |
| `useReport(id, type)` | `["report", id, type]` | `GET /api/{id}/reports/{type}` | on mount |
| `useHeatmap(id)` | `["heatmap", id]` | `GET /api/{id}/delays/heatmap` | on mount, staleTime 5min |
| `useLiveDelays(id)` | `["live", id]` | `GET /api/{id}/delays/live` | refetchInterval 30000 (toggleable) + manual |
| `useAsk()` | mutation | `POST /api/{id}/ask` | n/a |
| `useCreateAgency()` | mutation | `POST /agencies` | invalidates `["agencies"]` |

## Tab specs

### MapTab

- MapLibre map. Style from `VITE_MAP_STYLE_URL` (env var); when unset, uses a TypeScript constant defined in `src/api/client.ts` or `src/styles/tokens.ts` containing an OSM raster style object literal (no network fetch for the style itself, tile fetches still hit OSM).
- On heatmap data load: fit bounds to feature collection (with padding).
- **Heatmap layer:** circle layer keyed by stop. Radius scales with `samples` (clamped 4–20px). Color via stepped expression on `avg_delay_min` using the calm severity ramp (sage / sand / terracotta / brick).
- **Click stop:** popup shows stop_name, avg_delay_min, samples.
- **Empty state:** "ヒートマップデータがありません" + hint "集計を実行してください" with monospace `make analyze`.
- **Note:** `/delays/live` has no stop or coordinate info (only trip_id/route_code/scheduled_time/dep_delay), so live trips are NOT overlaid on the map. The Live tab table covers that data.

### AskTab

- Vertical chat-style. Input pinned to bottom; scrollable history above (in-memory, lost on reload — by design for v1).
- Suggested-prompt chips above input on first load: 3 constants (e.g., "今日の遅延ランキング", "系統5の遅延傾向", "雨天時の比較").
- On submit → `useAsk` mutation. Append user bubble, then assistant bubble with answer.
- Below each assistant bubble: `<details>詳細</details>` collapsing the `intent` JSON for debugging.
- Loading: skeleton bubble. Error: soft amber bubble with concise message + retry button (re-runs same question).

### LiveTab

- Header row: `自動更新` toggle (on by default, 30s interval), manual refresh button, `最終更新: HH:MM:SS` text (subtle gray).
- Table columns: route_code, service_type, scheduled_time (HH:MM), dep_delay (formatted as `±M分S秒` with calm severity color on text), captured_at (relative: `2分前`).
- Sortable client-side (click column headers). Default sort: dep_delay desc.
- Empty state: "リアルタイムデータがありません".

### ReportsTab

- Two-pane layout (left list 280px, right viewer):
  - Left: `useReports(id)` list. Each item: report_type label (mapped to JP), rendered_at as relative time. Active item highlighted.
  - Right: `useReport(id, type)` viewer. Shows `text` in monospace block (preserve newlines, max-width for readability). Below: `<details>` "ライブ再実行 ({rows.length}件)" rendering `rows` as a simple JSON-driven table.
- Deep-linkable: `/agencies/:id/reports/:type` selects the report.

## Header / Sidebar

**Header:**
- Left: app title `遅延ダッシュボード` (link to `/`)
- Center: `<AgencyPicker>` — single-agency mode shows label; multi-agency shows searchable combobox (URL-syncs `:id`)
- Right: `<SettingsButton>` → drawer with `<API key>` input (saves to `localStorage.api_key`)

**Sidebar (~180px desktop):**
- 4 items with inline-SVG icons, JP labels: 地図 / 質問 / リアルタイム / レポート
- Active state: filled bg + 3px left accent border in muted indigo
- Mobile (`<768px` media query): collapses to hamburger drawer

**Admin (`?admin=1`):**
- Floating button "+ 新規事業者" appears top-right of header
- Click → modal with `<AgencyForm>` (agency_name, feed_url, static_url)
- Submit → `useCreateAgency` mutation → invalidate `["agencies"]`

## Visual tone

| Token | Value |
|---|---|
| `bg-page` | `#fafaf8` |
| `bg-surface` | `#ffffff` |
| `border-soft` | `#eeeeee` |
| `text-primary` | `#2a2a2a` |
| `text-secondary` | `#6a6a6a` |
| `accent` | `#5b6cad` (muted indigo) |
| `delay-1` (<2min) | `#8fb88f` sage |
| `delay-2` (2–5min) | `#d4b878` sand |
| `delay-3` (5–10min) | `#c98a5e` terracotta |
| `delay-4` (>10min) | `#a85d52` brick |
| `error-bg` | `#fdf6e3` |

- Type: system font stack, 15px base, 1.6 line-height
- Spacing: 8px grid; default card padding 16–24px
- Motion: 150ms ease-out for hover/select; no pulsing/blinking
- Loading: skeleton blocks (gray rectangles), no spinners that grab focus
- Polling indicator: tiny "更新中..." text, fades in/out
- Errors: inline banner top-of-tab (never modal), one sentence + retry
- Empty states: centered icon + 1 reassuring sentence + 1 optional hint link

## Deployment

### Multistage Dockerfile (replaces current `Dockerfile`)

```dockerfile
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir poetry==1.8.5
COPY pyproject.toml poetry.lock ./
RUN poetry config virtualenvs.create false \
 && poetry install --only main --no-root --no-interaction
COPY . .
COPY --from=frontend /fe/dist /app/api/static
EXPOSE 8000
CMD ["sh","-c","uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```

### FastAPI static mount (`api/main.py`, AFTER all routers)

```python
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os.path

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
```

Dev mode (no `static/`): mount block skipped. Frontend runs on `:5173` via `npm run dev`; Vite proxy forwards `/api`, `/agencies`, `/health` to `http://localhost:8000`.

### Environment variables

| Var | Scope | Default | Purpose |
|---|---|---|---|
| `VITE_API_BASE_URL` | frontend build-time | `""` (same-origin) | Override for split-service deploys |
| `VITE_MAP_STYLE_URL` | frontend build-time | (unset → fall back to in-code OSM raster style object) | Swap to Carto/MapTiler/etc. |
| `CORS_ORIGINS` | backend runtime | `http://localhost:5173` | Dev only; prod is same-origin |
| `DATABASE_URL`, `GROQ_API_KEY` | backend runtime | (existing) | Unchanged |

### File modifications outside `frontend/`

- `Dockerfile` — replace with multistage above
- `api/main.py` — add static mount block at end
- `.gitignore` — add `frontend/node_modules`, `frontend/dist`, `api/static`, `.superpowers/`; carve out `!docs/superpowers/` so spec is tracked
- `.env.example` — add commented `VITE_API_BASE_URL=` and `VITE_MAP_STYLE_URL=`
- `README.md` — new "Frontend" section: dev server commands, env vars
- `Makefile` — add `frontend-dev` and `frontend-build` targets

## Testing

v1 ships without automated frontend tests. Validation:
- `npm run build` (TS strict + Vite build catches type/import errors)
- Manual smoke: load each tab against local backend with seeded data, verify network calls in devtools
- Vitest can be added later if frontend grows beyond v1 scope

## Out of scope (deferred)

- WebSocket chat tab
- i18n framework / English chrome
- Authentication
- Mobile-optimized layout
- Frontend test suite
- Theme toggle
- Realtime push (SSE/WS) for live tab — polling is sufficient

## Open questions

None at spec time. Implementation plan will surface package versions and any minor decisions (e.g., icon library — inline SVG or `lucide-react`).
