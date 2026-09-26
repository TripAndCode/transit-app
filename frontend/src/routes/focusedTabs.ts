/** Tabs that own their whole viewport: the banners, HelpHint and CopilotPanel
 *  are hidden on these so nothing competes with the visualization.
 *
 *  `overview` and `map` stay listed even though both now render only a
 *  redirect. React-router's declarative `<Navigate>` fires from an effect
 *  after the redirect element renders once, so the pathname is briefly the
 *  pre-redirect one — listing them avoids a one-frame flash of the chrome.
 *
 *  Anything routed here is a place CopilotPanel can never appear, which is
 *  why `COPILOT_ROUTES` is asserted disjoint from this set. */
export const FOCUSED_TAB_SEGMENTS = [
  "operations",
  "overview",
  "map",
  "route-analysis",
  "reports",
  "ask",
] as const;

export const FOCUSED_TAB_PATTERN = new RegExp(`/agencies/[^/]+/(${FOCUSED_TAB_SEGMENTS.join("|")})$`);
