# Design palette

`frontend/src/styles/global.css` is the canonical source for every token
below — read it directly for exact values and the invariants
`frontend/src/styles/tokens.test.ts` enforces; this page is an index, not a
copy to keep in sync by hand.

## One accent

The whole product — pre-auth and signed-in — uses a single accent, the
shell teal (`--accent`, with `--accent-strong` as its deepened variant for
button/active weight). Both stay in the same hue family in both themes;
`tokens.test.ts` asserts this so a themed page can't quietly reintroduce a
second brand color the way `.app-shell`'s old scoped override once did.
`--brand`/`--on-brand` are a separate, narrower identity (the operations
route badge and the pre-auth wordmark) — not a second accent.

## Per-theme severe

`--delay-severe` (the deep end of the delay-color ramp, `DELAY_RAMP.severe`
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
