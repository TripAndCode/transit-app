# Task 11 Report — Fix severe-color reactivity (literal `var()` for DOM, resolved accessor for MapLibre)

## What I implemented

Split the single theme-aware severe-color surface in `frontend/src/styles/tokens.ts`
into two, chosen by how the caller renders the color:

- **DOM/React consumers** get the **literal string** `"var(--delay-severe)"`. The
  browser cascade resolves it to the active theme's color automatically when rendered
  into an inline `style` prop — so these recolor on a theme toggle for free, no
  re-render, no JS.
- **MapLibre call sites** (which build plain-JS paint expressions that cannot consume
  `var()`) call a new exported `severeColorResolved()` that returns a real parseable
  hex.

### New/changed exported API of `tokens.ts`

- `DELAY_RAMP` — unchanged shape; `.ok`/`.mild`/`.moderate` keep their literal-typed
  hex constants (via `BASE_RAMP … as const`, preserved from Task 10). `.severe` is now
  the **plain literal constant** `"var(--delay-severe)"` in ALL cases (no conditional
  resolution). The whole object is `as const`, so `.severe` is typed as the literal.
- `delayColor(minutes: number): string` — **signature unchanged**. Only its internal
  `>10` branch's returned value changed, because that branch returns `DELAY_RAMP.severe`
  (now the literal var). `<=10` branches still return plain ramp hex.
- `relativeDelayColor(...)` — unchanged (never touches severe; returns `rgb(...)`).
- **New:** `severeColorResolved(): string` — the exact `getComputedStyle` logic that the
  old `severeColor()` getter had, with the same jsdom-fallback behavior: light default
  `"#d92121"` (the `SEVERE_FALLBACK` guard), dark `"#F04438"` when the cascade sets
  `--delay-severe`, and the `typeof document === "undefined"` SSR guard returning the
  fallback.

### Map hooks (5 call sites → `severeColorResolved()`)

- `frontend/src/tabs/map/useHeatmapLayer.ts` — 4 sites: the `clusterColor` and `colorExpr`
  `step` expressions in `buildLayers`, plus the two `setPaintProperty` recolor-effect
  expressions (dot + cluster). Effect still depends on `useThemeSignal()` so it rebuilds
  on toggle. Import updated to `{ DELAY_RAMP, severeColorResolved }`; stale comments
  referencing `DELAY_RAMP.severe` updated to `severeColorResolved()`.
- `frontend/src/tabs/map/useRouteOverlay.ts` — 1 site: the observed-stop `step`
  expression. Same import + comment updates.

### SVG presentation-attribute fixes (2 files)

- `frontend/src/components/charts/HourlyHeatmap.tsx:~178` (the task's known exception) —
  the heatmap cell `<rect>` rendered `fill={fill}` as an SVG presentation attribute,
  where `var()` does NOT resolve. Moved `fill` into the element's existing `style` prop
  (`style={{ fill, cursor: … }}`). The **legend swatch** (via the `Swatch` HTML `<span>`
  at the bottom of the file) already renders `background: color` in an inline `style` and
  was left untouched.
- `frontend/src/components/charts/DailyChart.tsx:~94` — **SCOPE ADDITION beyond the
  task's stated file list (see Self-review / escalation below).** The point `<circle>`
  rendered `fill={c}` (where `c = delayColor(...)`) as an SVG presentation attribute,
  the exact same broken pattern as the HourlyHeatmap exception. Moved `fill` into a
  `style={{ fill: c }}` prop.

## Verification that the 9 "don't touch" files render via `style` (not a presentation attribute)

Checked each file by reading the actual render site:

| File | Line | How the severe color is rendered | Verdict |
|---|---|---|---|
| `components/MapLegend.tsx` | 195 → `Row` @ 301-305 | `<span style={{ background: color }}>` (HTML) | style ✅ |
| `components/charts/HourlyHeatmap.tsx` (legend swatch) | 115 → `Swatch` @ 216-218 | `<span style={{ background: color }}>` (HTML) | style ✅ |
| `components/ReportTable.tsx` | 180 → `BarCell color=` | passed to `BarCell` (inline `style`) | style ✅ |
| `tabs/ForecastTab.tsx` | 195 / 267 / 344 | `style={{ background: … }}` (HTML divs/spans) | style ✅ |
| `tabs/NetworkTab.tsx` | 108 | `<span style={{ …, background: delayColor(...) }}>` (HTML) | style ✅ |
| `tabs/live/RouteRow.tsx` | 57 | `style={{ color: delayColor(...) }}` (HTML) | style ✅ |
| `tabs/live/RouteDrilldown.tsx` | 76 / 113 | `style={{ color … }}` / `style={{ background … }}` (HTML) | style ✅ |
| `components/paramPills/RoutePickerPill.tsx` | 151 | `<span style={{ background: delayColor(delay) }}>` (HTML) | style ✅ |

`components/charts/DailyChart.tsx` was ALSO on the "don't touch, renders via style" list —
but it did **not**: line 94 rendered `fill={c}` as an SVG presentation attribute. See
escalation below.

Also swept the whole `src/` tree for any other `fill={…}`/`stroke={…}`/`stopColor={…}`
presentation attributes that could receive a `var(--delay-severe)` value:
`InlineSparkline.tsx`, `PeakHourRibbon.tsx`, `Spinner.tsx` all use `fill={…}`/`stroke={…}`
but with colors that are NOT `delayColor()`/`DELAY_RAMP.severe`-derived (confirmed they do
not import those) — so they cannot receive `var(--delay-severe)` and are safe. The only two
presentation-attribute consumers of the severe var were HourlyHeatmap:178 and DailyChart:94,
both now fixed.

## What I tested & results

- **Focused suite** (task-specified):
  `npx vitest run src/styles/tokens.test.ts src/tabs/map/useHeatmapLayer.test.ts src/tabs/map/useRouteOverlay.test.ts src/tabs/map/useBasemapDim.test.ts`
  → **4 files, 24 tests passed.**
- **Full frontend suite:** `npm run test` → **24 files, 115 tests passed.**
- **Typecheck:** `npm run typecheck` (`tsc -b`) → clean.
- **Lint:** `npm run lint` → 0 errors (1 pre-existing warning in an untouched file,
  `pages/admin/__tests__/AdminOpsPage.test.tsx`).
- **i18n:** `npm run lint:i18n` → pass; `npm run lint:i18n-strings` → pass.
- **Build:** `npm run build` → built in ~8.4s, no errors (the >500 kB MapTab chunk
  warning is pre-existing and unrelated).
- **Manual live-toggle browser check** (plan step): NOT executed headlessly. The mechanism
  is verified indirectly: `tokens.test.ts` pins `DELAY_RAMP.severe === "var(--delay-severe)"`,
  `global.css` defines `--delay-severe` per theme (prior task), and the fixed DOM sites
  render it into inline `style`, so the cascade recolors them on `data-theme` flip without a
  re-render. Recommend a human confirm on the Reports Hourly Heatmap + Overview movers.

## TDD Evidence

### RED — tokens.test.ts (after rewriting the test, before implementing the split)

`cd frontend && npx vitest run src/styles/tokens.test.ts` →
```
FAIL  src/styles/tokens.test.ts > … delayColor(>10) returns the literal var(--delay-severe) string
  expect(delayColor(15)).toBe("var(--delay-severe)");   // received the resolved hex
FAIL  src/styles/tokens.test.ts > severeColorResolved() … 
  TypeError: severeColorResolved is not a function
Test Files  1 failed (1)   Tests  4 failed | 2 passed (6)
```

### RED — map hooks (after tokens split, before updating hook call sites)

`npx vitest run src/tabs/map/useHeatmapLayer.test.ts src/tabs/map/useRouteOverlay.test.ts` →
```
FAIL … useRouteOverlay … rebuilds the overlay with the dark severe color on themechange
  AssertionError: expected '…"var(--delay-severe)"…' to contain '#d92121'
Test Files  2 failed (2)   Tests  4 failed | 8 passed (12)
```
(The paint expressions now embedded the literal `"var(--delay-severe)"`, which MapLibre
can't parse — exactly why these sites must use `severeColorResolved()`.)

### GREEN — after implementing the tokens split + updating the 5 map call sites

`npx vitest run src/styles/tokens.test.ts src/tabs/map/useHeatmapLayer.test.ts src/tabs/map/useRouteOverlay.test.ts src/tabs/map/useBasemapDim.test.ts` →
```
Test Files  4 passed (4)   Tests  24 passed (24)
```

## Regression check — useBasemapDim.test.ts

`useBasemapDim.test.ts` (untouched; does not use `DELAY_RAMP`) was included in the focused
run above and passes as part of the 24/24. It is also part of the full 115/115 suite. ✅

## Files changed

- `frontend/src/styles/tokens.ts` — split into literal-`var()` `DELAY_RAMP.severe` +
  new exported `severeColorResolved()`.
- `frontend/src/styles/tokens.test.ts` — rewritten for the split (literal var for
  DELAY_RAMP.severe/delayColor; resolved hex for severeColorResolved).
- `frontend/src/tabs/map/useHeatmapLayer.ts` — 4 call sites → `severeColorResolved()`.
- `frontend/src/tabs/map/useHeatmapLayer.test.ts` — updated one stale comment.
- `frontend/src/tabs/map/useRouteOverlay.ts` — 1 call site → `severeColorResolved()`.
- `frontend/src/components/charts/HourlyHeatmap.tsx` — cell `fill` moved to `style`.
- `frontend/src/components/charts/DailyChart.tsx` — point `fill` moved to `style`
  (scope addition, see below).

## Self-review findings

- `DELAY_RAMP.severe` is the literal `"var(--delay-severe)"` in ALL cases (a plain
  constant, no conditional path). ✅
- `severeColorResolved()` preserves the old getter's exact behavior: `SEVERE_FALLBACK`
  `"#d92121"` light default, `"#F04438"` dark, SSR/`typeof document` guard. ✅
- Map-hook tests still verify **real rebuild-on-toggle** behavior (default build →
  `#d92121`; after `--delay-severe` set + `themechange` dispatch → `#F04438`); the
  expected hexes are exactly what `severeColorResolved()` produces, so the assertions
  are still correct with the value now sourced from the renamed function. ✅
- Naming: `severeColorResolved()` reads clearly as "give me a resolved (real) color",
  vs `DELAY_RAMP.severe` for the DOM literal. The `tokens.ts` block comment states
  explicitly which to call from new code. ✅

### ⚠️ Escalation: DailyChart.tsx was mis-listed as "renders via style"

The task instructed NOT to touch `DailyChart.tsx` and asserted it "already renders
`delayColor()`'s return value into an inline `style` prop." That is **false** —
`DailyChart.tsx:94` rendered the point color via the SVG presentation attribute
`fill={c}`. Since Task 11 makes `delayColor(>10)` return the literal `"var(--delay-severe)"`
(which does not resolve in a presentation attribute), leaving it untouched would ship a
regression: severe (>10 min) points on the daily chart would render with an unresolvable
`fill` (no/black fill). Per the task's escalation guidance ("report which file(s) need the
same move-to-style fix rather than silently expanding scope or silently leaving it broken"),
I applied the identical `fill → style` transform AND am flagging it here so it is not a
silent scope change. The task's file list was incomplete by exactly this one file.

## Concerns

- The live in-browser toggle smoke check was not run headlessly (see above) — worth a
  human eyeball on a severe-colored element in a non-map screen.
- One extra file (`DailyChart.tsx`) was modified beyond the stated scope for correctness;
  see escalation above.
