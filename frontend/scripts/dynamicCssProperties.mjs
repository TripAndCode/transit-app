// Custom properties that are genuinely set per-instance at runtime (an
// inline `style.setProperty`/style-object write), never declared in
// global.css by design — DailyChart/ChartEnter's measured stroke length,
// HourlyHeatmap/DowBandGrid's per-cell data-driven opacity, and MapTab's
// resizable queue-column width, and RevealSection's place in its tab's one
// entrance group. A literal fallback on one of these means
// "the instance hasn't set it yet", not "this design token is missing", so
// check-css-tokens exempts them even though nothing in global.css defines
// them. Anything else with an unresolved name and a literal fallback is
// presumed to be a typo'd or renamed design token (e.g. `var(--radius-md,
// 10px)` when only `--radius-lg` was ever defined) and must still resolve.
//
// This list is the one hole in that check, so it lives here rather than
// inside the checker: its exact membership is pinned by a test, and a name
// added to silence the check for a real token has to survive changing that
// test too.
export const DYNAMIC_PER_INSTANCE_PROPERTIES = new Set(["--len", "--cell-opacity", "--ops-queue-width", "--stagger"]);
