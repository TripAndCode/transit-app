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
  pulse: "p",
  routes: "r",
  time: "t",
  why: "w",
  compare: "c",
  live: "l",
  reports: "e",
  ask: "q",
};

const SUBLABEL_KEYS: Record<NavTo, string> = {
  pulse: "palette.nav_pulse_sublabel",
  routes: "palette.nav_routes_sublabel",
  time: "palette.nav_time_sublabel",
  why: "palette.nav_why_sublabel",
  compare: "palette.nav_compare_sublabel",
  live: "design:live",
  reports: "palette.nav_reports_sublabel",
  ask: "palette.nav_ask_sublabel",
};

/**
 * The palette's "Go to" group and `g`-chord destinations: every sidebar nav
 * item plus Ask (reached from the top bar rather than the rail, but still a
 * keyboard-reachable destination). Derived from
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
