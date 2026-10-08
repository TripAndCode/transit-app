# Design palette

`frontend/src/styles/global.css` is the canonical source for every token
below — read it directly for exact values and the invariants
`frontend/src/styles/tokens.test.ts` enforces; this page is an index, not a
copy to keep in sync by hand.

## One accent

The whole product — pre-auth and signed-in — uses a single accent,
signage blue: `--accent` (#2750C2, dark #86A2FF), with `--accent-soft`
(#E2E9FA, dark #1C2847) as its tint and `--accent-strong` (#1A378C, dark
#BCCBFF) as its deepened variant for button/active weight. It is for
interaction and selection only, never for delay magnitude, which has its
own ramp. `tokens.test.ts` asserts that `--accent` is declared only on the
two theme roots, so no scoped redefinition can fork the identity, and that
`--accent-strong` stays in the accent's hue family in both themes.
`--brand`/`--on-brand` are a separate, narrower identity (the operations
route badge and the pre-auth wordmark) — not a second accent.

## Ground and text

The ground is cool paper: `--bg-page` #F1F4F7, `--bg-surface` #FFFFFF and
`--bg-soft` #E8ECF1. Text is ink in three levels: `--text-primary`
#0F1A2A, `--text-secondary` #435166 and `--text-tertiary` #5B687D. The
dark theme redefines each. Every text level clears WCAG AA (4.5:1) on
surface, soft, page and the selected state (`--accent-soft`) in both themes,
and `tokens.test.ts` asserts every
pair.

## Delay ramp

`--d0`…`--d4` fill delay magnitude in five steps, calm to heavy — #D7EDE7,
#A6D5C7, #F0CD7A, #E39556 and #BC523A in the light theme — for < 1.5,
< 2.5, < 3.5, < 5 and ≥ 5 minutes. Each step has a dark counterpart, and
`--none` fills a cell with no observations. `tokens.test.ts` asserts the
five light values and that every step and `--none` exists in both themes.
Charts, tables and the map color delay through the `--delay-*` ramp
(`DELAY_RAMP` / `delayColor()` in `tokens.ts`), not these tokens, until
each is redesigned onto them.

## Per-theme severe

`--delay-severe` (the deep end of the `--delay-*` ramp, `DELAY_RAMP.severe`
/ `delayColor(>=5)`) is deliberately not the alarm-red `--color-danger`: it
is the loudest color in the product and still has to sit in a calm
interface. It is split per theme because no single hex clears WCAG AA
(4.5:1) as text on both a light and a near-black surface;
`tokens.test.ts` asserts each theme's value against its own `--bg-surface`
and asserts the cross-theme pairs fail, so the split can be retired the day
that stops being true. `tokens.ts` reads it via `getComputedStyle` so CSS
and MapLibre style-expression consumers resolve the same source of truth.

## Motion, elevation, and type scales

- `--dur-1`..`--dur-4` (150ms/240ms/600ms/1200ms): UI feedback, panel/menu
  open, a chart or bar drawing in, an ambient loop (skeleton pulse).
  `--ease-out` decelerates for things entering; `--ease-in-out` is
  symmetric, for things that move and settle. All four durations collapse
  to `0ms` under `prefers-reduced-motion`.
- `--el-1`..`--el-3`: three elevation tiers, each themed separately. Both
  themes pair a hairline ring with a drop shadow; dark additionally adds a
  top inset highlight to every tier, since a shadow alone barely reads on a
  near-black surface.
- `--text-xs`..`--text-3xl` (12px/13px/15px/17px/20px/26px/34px/46px):
  the type scale. `--text-xs` (12px) is the floor for any DOM text — below
  it, CJK glyphs stop being legible, so nothing in the product renders
  smaller.

## Type families

- `--font-body` and `--font-display` are both BIZ UDPGothic; display is the
  same family set at 700, so identity comes from weight and size rather
  than a second face.
- `--font-num` is Barlow Semi Condensed, which `.num` applies (with tabular
  figures) to every figure printed outside a table: hero values, stat tiles,
  chart labels and counters.
- `--font-mono` is IBM Plex Mono, for identifiers read character by
  character.
- `frontend/index.html` loads BIZ UDPGothic 400/700, Barlow Semi Condensed
  500/600/700 and IBM Plex Mono 400/500. `tokens.test.ts` asserts those
  weights and that `--font-display` is only set at a weight index.html
  loads.
