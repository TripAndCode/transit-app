import { SIDEBAR_NAV_ITEMS } from "./sidebarNavItems";

export type GoToTarget = {
  to: string;
  chordKey: string;
  labelKey: string;
  sublabelKey?: string;
};

type NavTo = (typeof SIDEBAR_NAV_ITEMS)[number]["to"] | "ask";

/** `g` + this letter jumps straight to the destination, both inside the
 *  palette and globally (see `handleGlobalKeyDown` in CommandPalette.tsx).
 *  Assigned here, not on `SIDEBAR_NAV_ITEMS`, since the sidebar itself has
 *  no notion of chords. */
const CHORD_KEYS: Record<NavTo, string> = {
  operations: "o",
  "period-overview": "p",
  "route-analysis": "a",
  network: "n",
  reports: "r",
  ask: "q",
};

const SUBLABEL_KEYS: Partial<Record<NavTo, string>> = {
  operations: "design:live",
  "route-analysis": "design:investigate",
  reports: "design:summary",
  ask: "palette.nav_ask_sublabel",
};

/**
 * The palette's "Go to" group and `g`-chord destinations: every sidebar nav
 * item plus Ask (the sidebar renders Ask as a distinct CTA, not a nav item,
 * but it's still a keyboard-reachable destination). Derived from
 * `SIDEBAR_NAV_ITEMS` so a new sidebar destination automatically gets a
 * palette entry instead of silently missing one.
 */
export const GO_TO_TARGETS: GoToTarget[] = [
  ...SIDEBAR_NAV_ITEMS.map(
    (item): GoToTarget => ({
      to: item.to,
      chordKey: CHORD_KEYS[item.to],
      labelKey: item.labelKey,
      sublabelKey: SUBLABEL_KEYS[item.to],
    }),
  ),
  { to: "ask", chordKey: CHORD_KEYS.ask, labelKey: "nav.ask", sublabelKey: SUBLABEL_KEYS.ask },
];
