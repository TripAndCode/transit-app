/** Tabs that own their whole viewport: the banners, HelpHint and CopilotPanel
 *  are hidden on these so nothing competes with the visualization.
 *
 *  `operations`, `overview`, `map`, `route-analysis` and `reports` stay
 *  listed only because they render a redirect. React-router's declarative
 *  `<Navigate>` fires from an effect after the redirect element renders once,
 *  so the pathname is briefly the pre-redirect one — listing them avoids a
 *  one-frame flash of the chrome.
 *
 *  Anything routed here is a place CopilotPanel can never appear, because
 *  App.tsx mounts it only as `{!focused && <CopilotPanel />}`.
 *  `CopilotPanel.routing.test.ts` asserts the panel's `COPILOT_INSIGHT_ROUTE`
 *  stays outside this set. */
export const FOCUSED_TAB_SEGMENTS = [
  "live",
  "operations",
  "overview",
  "map",
  "analysis/where",
  "route-analysis",
  "saved",
  "reports",
  "ask",
] as const;

/** A trailing slash is the same tab: react-router's matching ignores one, so
 *  the pattern must too or `/agencies/1/ask/` renders the tab with its chrome. */
export const FOCUSED_TAB_PATTERN = new RegExp(`/agencies/[^/]+/(${FOCUSED_TAB_SEGMENTS.join("|")})/?$`);
