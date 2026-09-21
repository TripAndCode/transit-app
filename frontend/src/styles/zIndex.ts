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
 */
export const Z_INDEX = {
  base: 0,
  raised: 1,
  mapOverlay: 5,
  dropdown: 20,
  popover: 30,
  sticky: 40,
  tooltip: 60,
  drawerBackdrop: 300,
  drawer: 301,
  modalBackdrop: 400,
  modal: 401,
  contextMenu: 500,
  toast: 600,
} as const;
