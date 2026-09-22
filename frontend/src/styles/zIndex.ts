/**
 * The app's single stacking-order ladder. Every fixed/absolutely-positioned
 * overlay orders itself against these named rungs instead of a fresh
 * literal, so a new overlay's place relative to existing ones is a decision
 * made once, here — not rediscovered by trial and error at each call site.
 *
 * Mirrored into `global.css`'s `:root` as `--z-<kebab-name>` custom
 * properties (same values) for CSS files that can't import this constant;
 * `zIndex.test.ts` fails if the two ever drift apart.
 *
 * `contextMenu` (500) deliberately outranks `modal`/`modalBackdrop`
 * (400/401): a context menu can be opened from inside a modal (e.g.
 * ThreadSidebar's conversation menu), and a menu opened from within a
 * surface must render above that surface, not under it.
 *
 * `coachMark` (70) sits just above `tooltip`: a coach mark points at an
 * element the user can still see and hover, so an incidental tooltip on
 * that element must not cover the thing explaining it. It stays below the
 * drawer and modal rungs, which are surfaces opened over the tour.
 *
 * `commandPalette` (550) sits above `contextMenu` rather than sharing it:
 * the palette opens from a global shortcut that fires while a context menu
 * is still open, and two surfaces on the same rung order by mount position
 * instead of intent, which would let the smaller one cover the palette.
 * `toast` stays above it — a notification must not be swallowed by a
 * surface the user opened on top of it.
 */
export const Z_INDEX = {
  base: 0,
  raised: 1,
  mapOverlay: 5,
  dropdown: 20,
  popover: 30,
  sticky: 40,
  tooltip: 60,
  coachMark: 70,
  drawerBackdrop: 300,
  drawer: 301,
  modalBackdrop: 400,
  modal: 401,
  contextMenu: 500,
  commandPalette: 550,
  toast: 600,
} as const;
