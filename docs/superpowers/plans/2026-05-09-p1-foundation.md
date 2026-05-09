# P1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fake stop-to-stop polylines with real road geometry from GTFS `shapes.txt`, give the app a serif headline + subtitle, move severity color from delay digits to leading dot, and ship two map quick-fixes (opacity floor + samples=1 filter). Phase 1 of the portfolio uplift in `docs/superpowers/specs/2026-05-09-portfolio-uplift-design.md`.

**Architecture:** Server-side: new `static_shapes(agency_id, shape_id, geom)` PostGIS table, populated by extending `pipeline/static_loader.py` to read `shapes.txt`. The existing `/route-shape` endpoint gains an optional `geometry: GeoJSON LineString | null` field; the frontend prefers it when present and falls back to the current stop-coordinate polyline. Identity changes are HTML/CSS-only: link to Noto Serif JP + Noto Sans JP, swap a leading severity dot in for the colored digit on Live cards, drop sidebar emojis for Lucide line icons.

**Tech Stack:** Python 3.12, FastAPI, asyncpg, psycopg2 (loader), PostGIS 14, MapLibre GL JS, React 18 + Vite, Pytest. The frontend has no test framework wired today; this plan does **not** add one (defer to a later phase) — frontend tasks are verified by `tsc --noEmit` plus a manual smoke checklist.

---

## File map

**Created:**

- `db/migrations/0005_static_shapes.up.sql` — new `static_shapes` table + GIST index
- `db/migrations/0005_static_shapes.down.sql` — drop
- `tests/fixtures/shapes_sample.txt` — 3-shape fixture for loader test
- `tests/fixtures/shapes_sample.zip` — minimal zip wrapping a single `shapes.txt`

**Modified:**

- `pipeline/static_loader.py` — add a `static_shapes` branch alongside `static_stops`
- `tests/test_static_loader.py` — assert shapes loaded, idempotent, missing-shapes-skipped
- `api/routers/map.py` — `/route-shape` joins `static_shapes`, adds `geometry` field
- `tests/test_api_map.py` — assert `geometry` returned when shapes loaded, `null` when absent
- `frontend/index.html` — Noto Serif JP + Noto Sans JP `<link>` tags
- `frontend/src/styles/tokens.ts` (or wherever `--font-display` lives) — define `--font-display` and `--font-body` CSS vars; ensure existing `delayColor` keeps working unchanged
- `frontend/src/components/Header.tsx` — apply serif font var to `<h1>`, add `リアルタイム × 時刻表` subtitle
- `frontend/src/tabs/LiveTab.tsx` — `Stat` component renders leading severity dot + neutral digit; round to minutes; add `title=` tooltip with full precision
- `frontend/src/tabs/MapTab.tsx` —
  - prefer `shape.geometry` over stop-coords polyline
  - opacity floor by severity in heatmap circle layer
  - filter `samples == 1` from heatmap source by default; legend toggle to re-enable
- `frontend/src/components/Sidebar.tsx` — replace emoji glyphs with Lucide icons
- `frontend/src/components/MapLegend.tsx` — add "1観測のみ表示" checkbox toggle
- `frontend/package.json` — add `lucide-react` dep

**Tests touched:** all changes have a corresponding test file modification listed above.

---

## Task 1: Migration 0005 — `static_shapes` table

**Files:**
- Create: `db/migrations/0005_static_shapes.up.sql`
- Create: `db/migrations/0005_static_shapes.down.sql`
- Modify: `tests/test_schema.py` (add a single assertion)

- [ ] **Step 1: Write the failing test**

Open `tests/test_schema.py` and add (place near existing static-table assertions):

```python
def test_static_shapes_table_exists(db_conn):
    """0005 migration should create static_shapes with a GIST index."""
    cur = db_conn.cursor()
    cur.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'static_shapes'
        """
    )
    assert cur.fetchone() is not None, "static_shapes table missing"

    cur.execute(
        """
        SELECT indexdef FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = 'static_shapes'
          AND indexdef ILIKE '%USING gist%'
        """
    )
    assert cur.fetchone() is not None, "GIST index on static_shapes missing"
```

- [ ] **Step 2: Run test to verify it fails**

```
poetry run pytest tests/test_schema.py::test_static_shapes_table_exists -v
```

Expected: FAIL — `static_shapes table missing`.

- [ ] **Step 3: Create the up migration**

Create `db/migrations/0005_static_shapes.up.sql`:

```sql
CREATE TABLE IF NOT EXISTS static_shapes (
    agency_id INT NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    shape_id  TEXT NOT NULL,
    geom      geometry(LineString, 4326) NOT NULL,
    PRIMARY KEY (agency_id, shape_id)
);

CREATE INDEX IF NOT EXISTS idx_static_shapes_geom
    ON static_shapes USING GIST (geom);
```

- [ ] **Step 4: Create the down migration**

Create `db/migrations/0005_static_shapes.down.sql`:

```sql
DROP INDEX IF EXISTS idx_static_shapes_geom;
DROP TABLE IF EXISTS static_shapes;
```

- [ ] **Step 5: Apply the migration to the test database**

```
poetry run python gtfs_pipeline.py migrate up
```

Expected output: `applied 0005_static_shapes`.

- [ ] **Step 6: Run the test to verify it passes**

```
poetry run pytest tests/test_schema.py::test_static_shapes_table_exists -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```
git add db/migrations/0005_static_shapes.up.sql db/migrations/0005_static_shapes.down.sql tests/test_schema.py
git commit -m "feat(db): add static_shapes(agency_id, shape_id, geom) table with GIST index"
```

---

## Task 2: Pipeline — load `shapes.txt` into `static_shapes`

**Files:**
- Create: `tests/fixtures/shapes_sample.txt` (CSV content)
- Create: `tests/fixtures/static_with_shapes.zip` (zip containing `shapes.txt`)
- Modify: `pipeline/static_loader.py`
- Modify: `tests/test_static_loader.py`

- [ ] **Step 1: Create the test fixture (CSV)**

Create `tests/fixtures/shapes_sample.txt`:

```
shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence
S1,40.8200,140.7400,1
S1,40.8210,140.7410,2
S1,40.8220,140.7420,3
S2,40.8300,140.7500,1
S2,40.8310,140.7510,2
S2,40.8320,140.7520,3
S3,40.8400,140.7600,1
S3,40.8410,140.7610,2
```

- [ ] **Step 2: Create the test fixture (zip wrapping the CSV)**

Run from the repo root:

```bash
poetry run python - <<'PY'
import zipfile, pathlib
src = pathlib.Path("tests/fixtures/shapes_sample.txt").read_bytes()
zip_path = pathlib.Path("tests/fixtures/static_with_shapes.zip")
zip_path.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("shapes.txt", src)
print(f"wrote {zip_path}")
PY
```

Expected: `wrote tests/fixtures/static_with_shapes.zip`.

- [ ] **Step 3: Write the failing test**

Add to `tests/test_static_loader.py` (after existing tests):

```python
def test_load_static_shapes_builds_linestrings(db_conn, tmp_agency):
    from pipeline.static_loader import load_static
    load_static("tests/fixtures/static_with_shapes.zip", tmp_agency, db_conn)

    cur = db_conn.cursor()
    cur.execute(
        "SELECT shape_id, ST_AsText(geom), ST_NumPoints(geom) "
        "FROM static_shapes WHERE agency_id = %s ORDER BY shape_id",
        (tmp_agency,),
    )
    rows = cur.fetchall()

    assert [r[0] for r in rows] == ["S1", "S2", "S3"]
    assert [r[2] for r in rows] == [3, 3, 2]
    # First point of S1 = (lon=140.7400, lat=40.8200)
    assert rows[0][1].startswith("LINESTRING(140.74 40.82,")


def test_load_static_shapes_idempotent(db_conn, tmp_agency):
    from pipeline.static_loader import load_static
    load_static("tests/fixtures/static_with_shapes.zip", tmp_agency, db_conn)
    load_static("tests/fixtures/static_with_shapes.zip", tmp_agency, db_conn)  # second run

    cur = db_conn.cursor()
    cur.execute("SELECT COUNT(*) FROM static_shapes WHERE agency_id = %s", (tmp_agency,))
    assert cur.fetchone()[0] == 3, "second load must replace, not duplicate"


def test_load_static_zip_without_shapes_succeeds(db_conn, tmp_agency, capsys):
    """A static zip lacking shapes.txt must still load other tables and log a skip."""
    # Re-use any existing static fixture in the repo that DOESN'T contain shapes.txt.
    # If the only fixture you find DOES contain shapes, build a temporary zip in this
    # test that wraps just stops.txt + trips.txt.
    from pipeline.static_loader import load_static
    load_static("tests/fixtures/static_no_shapes.zip", tmp_agency, db_conn)
    out = capsys.readouterr().out
    assert "shapes.txt not in zip — skipped" in out

    cur = db_conn.cursor()
    cur.execute("SELECT COUNT(*) FROM static_shapes WHERE agency_id = %s", (tmp_agency,))
    assert cur.fetchone()[0] == 0
```

**Build the no-shapes zip fixture now, before running the test:**

```bash
poetry run python - <<'PY'
import zipfile, pathlib
zip_path = pathlib.Path("tests/fixtures/static_no_shapes.zip")
zip_path.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("stops.txt",
               "stop_id,stop_name,stop_lat,stop_lon\n"
               "S001,駅前,40.82,140.74\n")
    z.writestr("trips.txt",
               "trip_id,route_id,trip_headsign,shape_id\n"
               "T001,R1,Loop,\n")
print(f"wrote {zip_path}")
PY
```

If `tmp_agency` is not already defined in `tests/conftest.py`, add a session-scoped fixture that inserts a temp `agencies` row (use a high id like `99001` to avoid colliding with seeded agencies), yields the id, and deletes the row on teardown. Pattern to copy: any existing fixture in `tests/conftest.py` that creates a temp DB row.

- [ ] **Step 4: Run tests to verify they fail**

```
poetry run pytest tests/test_static_loader.py -k "static_shapes or zip_without_shapes" -v
```

Expected: FAIL — `static_shapes` is empty (loader doesn't write to it yet).

- [ ] **Step 5: Extend the loader**

In `pipeline/static_loader.py`, add `shapes.txt` to `_STATIC_FILE_MAP` and add a `static_shapes` branch in the per-file dispatch.

Add to `_STATIC_FILE_MAP` (alongside the existing entries):

```python
("shapes.txt", "static_shapes", ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"]),
```

Inside `load_static`, after the existing `if table == "static_stops": ...` branch and before the generic `else:` branch, add:

```python
            elif table == "static_shapes":
                # Group raw_rows by shape_id, keep ordering by pt_sequence (int).
                # Build a LineString per shape via ST_MakeLine over ordered points.
                from collections import defaultdict
                by_shape: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
                for row in raw_rows:
                    shape_id = row[0]
                    try:
                        lat = float(row[1])
                        lon = float(row[2])
                        seq = int(row[3])
                    except (TypeError, ValueError):
                        continue  # skip malformed
                    if shape_id:
                        by_shape[shape_id].append((seq, lon, lat))

                for shape_id, pts in by_shape.items():
                    pts.sort(key=lambda t: t[0])
                    if len(pts) < 2:
                        # LineString needs ≥2 points; skip degenerate shapes.
                        continue
                    # Emit an SQL array of points; let PostGIS build the LineString.
                    flat: list[float] = []
                    for _, lon, lat in pts:
                        flat.extend([lon, lat])
                    placeholders = ",".join(
                        "ST_MakePoint(%s, %s)" for _ in range(len(pts))
                    )
                    cur.execute(
                        f"INSERT INTO static_shapes (agency_id, shape_id, geom) "
                        f"VALUES (%s, %s, ST_SetSRID(ST_MakeLine(ARRAY[{placeholders}]), 4326)) "
                        f"ON CONFLICT (agency_id, shape_id) DO UPDATE SET geom = EXCLUDED.geom",
                        [agency_id, shape_id, *flat],
                    )
```

The pre-existing `cur.execute(f"DELETE FROM {table} WHERE agency_id = %s", (agency_id,))` call at the top of the loop already clears prior shapes, so idempotency is automatic.

- [ ] **Step 6: Run tests to verify they pass**

```
poetry run pytest tests/test_static_loader.py -k "static_shapes or zip_without_shapes" -v
```

Expected: 3 PASS.

- [ ] **Step 7: Run the full loader test file to confirm no regressions**

```
poetry run pytest tests/test_static_loader.py -v
```

Expected: all PASS.

- [ ] **Step 8: Commit**

```
git add pipeline/static_loader.py tests/test_static_loader.py tests/fixtures/shapes_sample.txt tests/fixtures/static_with_shapes.zip tests/fixtures/static_no_shapes.zip
git commit -m "feat(pipeline): load GTFS shapes.txt into static_shapes as PostGIS LineStrings"
```

---

## Task 3: API — `/route-shape` returns optional `geometry`

**Files:**
- Modify: `api/routers/map.py` (function `route_shape` at line 54)
- Modify: `tests/test_api_map.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_api_map.py`:

```python
@pytest.mark.asyncio
async def test_route_shape_returns_geometry_when_shapes_loaded(
    client, agency_with_shapes, route_with_shape_id
):
    """When static_trips.shape_id resolves to a static_shapes row, the
    response includes a GeoJSON LineString built from the shape's geom."""
    resp = await client.get(
        f"/api/map/route-shape?route={route_with_shape_id}",
        headers={"X-Agency-Id": str(agency_with_shapes)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["geometry"] is not None
    assert body["geometry"]["type"] == "LineString"
    assert isinstance(body["geometry"]["coordinates"], list)
    assert len(body["geometry"]["coordinates"]) >= 2
    # Coordinates are [lon, lat] pairs in the LineString
    lon, lat = body["geometry"]["coordinates"][0]
    assert 139.0 < lon < 142.0
    assert 39.0 < lat < 42.0


@pytest.mark.asyncio
async def test_route_shape_returns_null_geometry_when_no_shapes_loaded(
    client, agency_without_shapes, route_with_shape_id_but_missing_shape_row
):
    """If the route's trips have a shape_id but static_shapes has no matching
    row (loader skipped, agency lacks shapes.txt), geometry is null."""
    resp = await client.get(
        f"/api/map/route-shape?route={route_with_shape_id_but_missing_shape_row}",
        headers={"X-Agency-Id": str(agency_without_shapes)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["geometry"] is None
    # stops list still populated — frontend falls back to stop-coord polyline
    assert len(body["stops"]) >= 2
```

If the listed fixtures do not exist in `tests/conftest.py`, add them. The pattern: insert a temp agency row, insert one `static_trips` row referencing a `shape_id`, conditionally insert the corresponding `static_shapes` row, insert minimal `updates` rows so the existing dedup CTE in `route_shape` returns at least 2 stops, yield agency-id and route-code, teardown.

- [ ] **Step 2: Run tests to verify they fail**

```
poetry run pytest tests/test_api_map.py -k "route_shape_returns" -v
```

Expected: FAIL — `KeyError: 'geometry'` (field doesn't exist yet).

- [ ] **Step 3: Modify `route_shape` to join `static_shapes` and add `geometry` to the response**

Open `api/routers/map.py`. Replace the `route_shape` body starting at line 54.

Above the existing `WITH dedup AS (...)` query, add a separate query to fetch the most-frequent shape's geometry for this `(agency_id, route_code)`:

```python
    geom_row = await conn.fetchrow(
        """
        WITH ranked AS (
            SELECT t.shape_id, COUNT(*) AS n
            FROM static_trips t
            WHERE t.agency_id = $1
              AND t.route_id = $2
              AND t.shape_id IS NOT NULL
              AND t.shape_id <> ''
            GROUP BY t.shape_id
            ORDER BY n DESC
            LIMIT 1
        )
        SELECT ST_AsGeoJSON(s.geom)::json AS geom_json
        FROM ranked r
        JOIN static_shapes s
          ON s.agency_id = $1 AND s.shape_id = r.shape_id
        """,
        agency_id,
        route,
    )
    geometry = geom_row["geom_json"] if geom_row else None
```

Then in the `return {...}` dict, add the `geometry` field at the top:

```python
    return {
        "route": route,
        "geometry": geometry,  # GeoJSON LineString or None
        "stops": [
            ...  # unchanged
        ],
    }
```

Note: `ST_AsGeoJSON` returns a string by default — the `::json` cast converts it so asyncpg surfaces a dict directly.

- [ ] **Step 4: Run tests to verify they pass**

```
poetry run pytest tests/test_api_map.py -k "route_shape_returns" -v
```

Expected: 2 PASS.

- [ ] **Step 5: Run the full map-router test file to catch regressions**

```
poetry run pytest tests/test_api_map.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```
git add api/routers/map.py tests/test_api_map.py tests/conftest.py
git commit -m "feat(api): /route-shape returns GeoJSON geometry when shape loaded; null fallback"
```

---

## Task 4: Frontend — MapTab uses `geometry` when present

**Files:**
- Modify: `frontend/src/tabs/MapTab.tsx`

- [ ] **Step 1: Locate the polyline construction**

Open `frontend/src/tabs/MapTab.tsx`. Find the line near 240 that currently reads:

```tsx
const coords: [number, number][] = shape.stops.map((s) => [s.lon, s.lat]);
```

- [ ] **Step 2: Update the type for the route-shape response**

Find the `RouteShape` (or equivalent) type/interface used to type the fetched `shape` object. Add a `geometry` field:

```tsx
type RouteShape = {
  route: string;
  geometry: { type: "LineString"; coordinates: [number, number][] } | null;
  stops: RouteShapeStop[];
};
```

If the type is inferred from a `useQuery` call without an explicit type, declare `RouteShape` and pass it as the generic.

- [ ] **Step 3: Prefer `geometry` over stop-derived coords**

Replace the `coords` line with:

```tsx
const coords: [number, number][] =
  shape.geometry?.coordinates ?? shape.stops.map((s) => [s.lon, s.lat]);
```

Leave the rest of the route-line GeoJSON construction (around line 251) unchanged — it already wraps `coords` into a `LineString` feature.

- [ ] **Step 4: Verify with typecheck**

```
cd frontend && npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 5: Manual smoke check**

```
make db && make seed-agencies && make fetch-ingest
make serve   # terminal A
make frontend-dev   # terminal B
```

In a browser at `http://localhost:5173`:
1. Navigate to the Map tab for an agency whose static GTFS includes `shapes.txt` (Aomori does).
2. Filter to a single route.
3. Confirm the route polyline now hugs the road geometry (curves, not straight lines between stops).
4. Repeat for an agency lacking `shapes.txt` — confirm the polyline still renders (falls back to stop-coordinate connect-the-dots).

- [ ] **Step 6: Commit**

```
git add frontend/src/tabs/MapTab.tsx
git commit -m "feat(map): prefer GTFS shape geometry over stop-coord polyline; fallback unchanged"
```

---

## Task 5: Identity — Noto Serif JP + Noto Sans JP fonts

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/src/styles/tokens.ts` (or wherever font-family CSS vars live; if no such file exists today, modify `frontend/src/main.tsx`'s root style block or the global stylesheet `frontend/src/index.css`)

- [ ] **Step 1: Add font `<link>` tags to `index.html`**

Open `frontend/index.html`. In `<head>` (after the existing `<link rel="icon">`, before `<title>`), insert:

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600&family=Noto+Serif+JP:wght@500;600;700&display=swap" rel="stylesheet">
```

- [ ] **Step 2: Define font-family CSS vars**

If `frontend/src/styles/tokens.ts` exports a `:root { ... }` style string, add to it. Otherwise add to `frontend/src/index.css` (or whichever global CSS file the app already loads):

```css
:root {
  --font-display: "Noto Serif JP", "Times New Roman", serif;
  --font-body: "Noto Sans JP", system-ui, -apple-system, sans-serif;
}

body {
  font-family: var(--font-body);
}
```

- [ ] **Step 3: Verify with typecheck + dev build**

```
cd frontend && npx tsc -b --noEmit && npm run build
```

Expected: build succeeds.

- [ ] **Step 4: Manual smoke check**

```
make frontend-dev
```

Open the app. With devtools Network tab, confirm `fonts.gstatic.com` requests succeed. Body text should render in Noto Sans JP (subtle but visible — kanji glyphs are rounder than the system default).

- [ ] **Step 5: Commit**

```
git add frontend/index.html frontend/src/styles/tokens.ts frontend/src/index.css
git commit -m "feat(ui): load Noto Serif JP + Noto Sans JP, define --font-display/--font-body"
```

(Adjust the staged files to match what you actually touched in Step 2.)

---

## Task 6: Identity — serif headline + subtitle

**Files:**
- Modify: `frontend/src/components/Header.tsx`

- [ ] **Step 1: Read the current Header structure**

Open `frontend/src/components/Header.tsx`. Locate the `<h1>` (or equivalent title element) that currently renders `遅延ダッシュボード`.

- [ ] **Step 2: Apply serif font + add subtitle**

Replace the existing title block with:

```tsx
<div style={{ display: "flex", flexDirection: "column", lineHeight: 1.1 }}>
  <h1
    style={{
      fontFamily: "var(--font-display)",
      fontWeight: 600,
      fontSize: 20,
      margin: 0,
      letterSpacing: "0.01em",
    }}
  >
    遅延ダッシュボード
  </h1>
  <span
    style={{
      fontFamily: "var(--font-display)",
      fontSize: 11,
      color: "var(--text-tertiary)",
      marginTop: 2,
      letterSpacing: "0.04em",
    }}
  >
    リアルタイム × 時刻表
  </span>
</div>
```

Preserve any existing flex/layout wrapper around the title — only the inner title-and-subtitle markup changes.

- [ ] **Step 3: Verify with typecheck**

```
cd frontend && npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 4: Manual smoke check**

Open the dev server. Confirm:
- Headline now renders in Noto Serif JP (visibly different from prior sans).
- Subtitle `リアルタイム × 時刻表` appears underneath in smaller serif.
- Header height did not break the page layout (sidebar still aligns; tab bar still sits below).

- [ ] **Step 5: Commit**

```
git add frontend/src/components/Header.tsx
git commit -m "feat(ui): serif headline + リアルタイム × 時刻表 subtitle"
```

---

## Task 7: Identity — leading severity dot + minute-rounded digit

**Files:**
- Modify: `frontend/src/tabs/LiveTab.tsx`

- [ ] **Step 1: Inspect current `Stat` and `formatDelay` (read-only)**

Open `frontend/src/tabs/LiveTab.tsx`. The current `Stat` (line 185) renders the value with `color`. The current `formatDelay` (line 194) returns `+12分34秒` style strings.

- [ ] **Step 2: Add `formatDelayMinutesRounded`**

Below `formatDelay`, add:

```tsx
function formatDelayMinutesRounded(seconds: number): string {
  if (seconds === 0) return "定刻";
  const sign = seconds < 0 ? "-" : "+";
  const minutes = Math.round(Math.abs(seconds) / 60);
  return `${sign}${minutes}分`;
}
```

- [ ] **Step 3: Update `Stat` to render a leading dot + neutral digit + tooltip**

Replace the existing `Stat` component with:

```tsx
function Stat({
  label,
  value,
  fullPrecision,
  dotColor,
}: {
  label: string;
  value: string;
  fullPrecision: string;
  dotColor: string;
}) {
  return (
    <div title={fullPrecision}>
      <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 2 }}>
        {label}
      </div>
      <div
        style={{
          fontSize: 16,
          fontWeight: 600,
          color: "var(--text-primary)",
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <span aria-hidden="true" style={{ color: dotColor, fontSize: 10, lineHeight: 1 }}>
          ●
        </span>
        <span>{value}</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Update the two `<Stat ...>` call sites**

Replace lines 175–176:

```tsx
<Stat
  label="平均"
  value={formatDelayMinutesRounded(card.avg_delay_sec)}
  fullPrecision={formatDelay(card.avg_delay_sec)}
  dotColor={delayColor(avgMin)}
/>
<Stat
  label="最大"
  value={formatDelayMinutesRounded(card.worst_delay_sec)}
  fullPrecision={formatDelay(card.worst_delay_sec)}
  dotColor={delayColor(worstMin)}
/>
```

- [ ] **Step 5: Verify with typecheck**

```
cd frontend && npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 6: Manual smoke check**

Open the Live tab in the dev server. For a route with non-zero delay, confirm:
- A small colored dot precedes the digit (severity color preserved on the dot).
- The digit itself is the neutral primary text color, not red/orange.
- Hover the value: tooltip shows the full `+M分S秒` string.
- For 定刻 routes, the digit reads `定刻` and the dot is the lowest-severity color (sage).

- [ ] **Step 7: Commit**

```
git add frontend/src/tabs/LiveTab.tsx
git commit -m "feat(live): severity moves to leading dot; digits round to 分; full precision on hover"
```

---

## Task 8: Identity — sidebar emojis → Lucide icons

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/src/components/Sidebar.tsx`

- [ ] **Step 1: Add `lucide-react` dependency**

```
cd frontend && npm install lucide-react@^0.468.0
```

(Pin a known-stable major; latest at the time of writing is fine. Update `frontend/package.json` and `package-lock.json` together.)

- [ ] **Step 2: Read current Sidebar emoji glyphs (read-only)**

Open `frontend/src/components/Sidebar.tsx`. Locate the emoji characters used as nav icons (likely literal emoji in JSX).

- [ ] **Step 3: Replace each emoji with a Lucide icon**

Map each tab to a Lucide icon (final mapping is your call; recommended starting point):

| Tab | Lucide icon | Import name |
|---|---|---|
| 地図 (Map) | `Map` | `Map` |
| 質問 (Ask) | `MessageSquare` | `MessageSquare` |
| リアルタイム (Live) | `Activity` | `Activity` |
| レポート (Reports) | `BarChart3` | `BarChart3` |

At the top of `Sidebar.tsx`:

```tsx
import { Map as MapIcon, MessageSquare, Activity, BarChart3 } from "lucide-react";
```

(Renamed `Map` to `MapIcon` to avoid colliding with the JS built-in `Map`.)

In the nav-item JSX, replace each emoji with the corresponding icon component:

```tsx
<MapIcon size={18} strokeWidth={1.5} />
<MessageSquare size={18} strokeWidth={1.5} />
<Activity size={18} strokeWidth={1.5} />
<BarChart3 size={18} strokeWidth={1.5} />
```

`strokeWidth={1.5}` keeps the icons thin and calm — matches the project's calm-UI rule (memory: `feedback_calm_ui.md`).

- [ ] **Step 4: Verify with typecheck + build**

```
cd frontend && npx tsc -b --noEmit && npm run build
```

Expected: clean.

- [ ] **Step 5: Manual smoke check**

Open the dev server. Sidebar should now show four thin line icons instead of emojis. Hover/active states still highlight as before.

- [ ] **Step 6: Commit**

```
git add frontend/package.json frontend/package-lock.json frontend/src/components/Sidebar.tsx
git commit -m "feat(ui): swap sidebar emojis for Lucide line icons"
```

---

## Task 9: Map quick-fixes — opacity floor + samples=1 filter

**Files:**
- Modify: `frontend/src/tabs/MapTab.tsx` (heatmap circle layer paint expression + source-data preprocessing)
- Modify: `frontend/src/components/MapLegend.tsx` (add toggle)

- [ ] **Step 1: Read the current heatmap circle layer config (read-only)**

In `MapTab.tsx`, find the `circle-opacity` expression for the heatmap circle layer (was around line 177–184 in the spec audit). The current expression scales opacity by `samples`, which causes severe-delay stops with low sample counts to render near-invisible.

- [ ] **Step 2: Replace `circle-opacity` with a severity-floored expression**

Replace the existing `circle-opacity` expression with:

```ts
"circle-opacity": [
  "max",
  // Severity floor: stops with avg_min ≥ 10 always render at ≥ 0.7 opacity,
  // ≥ 5 at ≥ 0.55, otherwise scale by samples down to 0.35.
  [
    "case",
    [">=", ["get", "avg_min"], 10], 0.7,
    [">=", ["get", "avg_min"], 5], 0.55,
    0.0
  ],
  // Sample-based scaling (existing behavior for low-severity stops)
  [
    "interpolate", ["linear"], ["get", "samples"],
    1, 0.35,
    20, 0.85
  ]
],
```

- [ ] **Step 3: Add the legend toggle**

In `MapLegend.tsx`, add (above the existing severity ramp legend body):

```tsx
<label
  style={{
    display: "flex",
    alignItems: "center",
    gap: 6,
    fontSize: 11,
    color: "var(--text-secondary)",
    cursor: "pointer",
    marginBottom: 8,
  }}
>
  <input
    type="checkbox"
    checked={showSingleSampleStops}
    onChange={(e) => onShowSingleSampleStopsChange(e.target.checked)}
  />
  1観測のみも表示
</label>
```

Add `showSingleSampleStops: boolean` and `onShowSingleSampleStopsChange: (v: boolean) => void` to the `MapLegend` props type. Default `false`.

- [ ] **Step 4: Wire the toggle in `MapTab.tsx`**

In `MapTab.tsx`:

1. Add state: `const [showSingleSampleStops, setShowSingleSampleStops] = useState(false);`
2. Filter the heatmap source `features` array before setting the source data:
   ```tsx
   const filtered = showSingleSampleStops
     ? rawFeatures
     : rawFeatures.filter((f) => (f.properties?.samples ?? 0) >= 2);
   ```
   (Replace `rawFeatures` with whatever the current variable name is for the unfiltered features list.)
3. Pass props to `<MapLegend ... />`:
   ```tsx
   <MapLegend
     {...existingProps}
     showSingleSampleStops={showSingleSampleStops}
     onShowSingleSampleStopsChange={setShowSingleSampleStops}
   />
   ```
4. When `showSingleSampleStops` changes, the filtered array changes, and the existing `setData` effect on the heatmap source already re-pushes data to MapLibre.

- [ ] **Step 5: Verify with typecheck**

```
cd frontend && npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 6: Manual smoke check**

In the dev server's Map tab:
- Confirm a known severe-delay stop (≥ 10 min) is now visibly opaque even with low sample count (it was buried before).
- Confirm `samples = 1` stops are absent by default; toggling the legend checkbox brings them back.
- Confirm the toggle's state survives a tab switch (Map → Ask → Map). If it does not, lift the state into the URL persistence layer that already drives other filters; otherwise leave as session-only state.

- [ ] **Step 7: Commit**

```
git add frontend/src/tabs/MapTab.tsx frontend/src/components/MapLegend.tsx
git commit -m "fix(map): opacity floor by severity; filter samples=1 with legend toggle"
```

---

## Task 10: Verification

**Files:** none modified — verification only.

- [ ] **Step 1: Run the full backend test suite**

```
poetry run pytest -v
```

Expected: all PASS. If any test fails that is unrelated to this plan, note it but do not fix in this plan — open a separate issue.

- [ ] **Step 2: Run the frontend typecheck and build**

```
cd frontend && npx tsc -b --noEmit && npm run build
```

Expected: clean.

- [ ] **Step 3: End-to-end smoke against a real agency**

```
make db
make seed-agencies
make fetch-ingest    # ingest real Aomori GTFS-RT + static
make serve           # terminal A
make frontend-dev    # terminal B
```

In a browser, walk through:

| Tab | Check |
|---|---|
| Header | Serif `遅延ダッシュボード`, subtitle `リアルタイム × 時刻表` visible |
| Sidebar | Lucide line icons (no emoji) |
| Map | Single-route polyline hugs roads (not zigzag); severe stops opaque; `samples = 1` hidden; toggle restores them |
| Live | Stat values: leading colored dot + neutral digit, rounded to 分; tooltip shows full precision |
| Ask, Reports | Unchanged behavior — confirm nothing broke |

- [ ] **Step 4: Verify no regressions in cron / API health**

```
curl -fsS http://localhost:8000/health
```

Expected: 200 with `{"status":"ok"}` (or whatever the existing shape is).

- [ ] **Step 5: Final commit (if any cleanup)**

If any minor format / lint changes accumulated, commit them as `chore: post-P1 cleanup`. Otherwise skip.

```
git status
git add -A
git commit -m "chore: post-P1 cleanup" || echo "nothing to commit"
```

---

## Spec coverage

| Spec section | Task |
|---|---|
| P1a — `static_shapes` schema | Task 1 |
| P1a — loader for `shapes.txt` | Task 2 |
| P1a — `/route-shape` joins shapes | Task 3 |
| P1a — frontend uses real geometry | Task 4 |
| P1b — Noto Serif JP + Noto Sans JP | Task 5 |
| P1b — subtitle `リアルタイム × 時刻表` | Task 6 |
| P1b — severity dot move + minute round + tooltip | Task 7 |
| P1b — Lucide icons | Task 8 |
| P1c — opacity floor by severity | Task 9 |
| P1c — `samples = 1` filter + toggle | Task 9 |
| P1 tests — `test_static_loader.py` | Task 2 |
| P1 tests — `test_route_geometry` (covered in `test_api_map.py`) | Task 3 |
| P1 tests — Live card markup snapshot | Manual (no frontend test framework yet — deferred per Tech Stack note) |
| Final verification | Task 10 |
