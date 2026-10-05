// @vitest-environment node
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";

/**
 * Structural guard for the one-page-header rule: a routed page states its
 * name once, through the shared `PageHeader`, so the `<h1>`, its eyebrow and
 * its actions slot cannot drift apart page by page.
 *
 * A source scan rather than a render pass because the invariant is about the
 * page's own markup, and rendering every routed page would need each one's
 * data hooks mocked — the cost of which is what let five title treatments
 * accumulate unnoticed in the first place.
 */
const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");

/** Every element `main.tsx` mounts under `<App />` that owns a page title. */
const SHELL_PAGES = [
  "tabs/MapTab.tsx",
  "tabs/OverviewTab.tsx",
  "tabs/AskTab.tsx",
  "tabs/AnalysisTab.tsx",
  "tabs/RouteAnalysisTab.tsx",
  "tabs/ReportsHomeTab.tsx",
  "tabs/NetworkTab.tsx",
  "pages/AccountPage.tsx",
  "pages/HelpPage.tsx",
  "pages/admin/AdminBoardPage.tsx",
  "pages/admin/AdminAgenciesPage.tsx",
  "pages/admin/AdminUsersPage.tsx",
  "pages/admin/AdminOpsPage.tsx",
  "pages/admin/AdminAskOpsPage.tsx",
  "pages/admin/AdminArchitecturePage.tsx",
  "pages/admin/AdminAuditPage.tsx",
  "pages/admin/AdminFlagsPage.tsx",
];

/**
 * Routed elements that keep their own `<h1>`, and the reason each is not a
 * page inside the app shell. `PageHeader` states a page's name in the
 * document flow above its content; none of these is that.
 */
const OWN_TITLE = {
  "pages/LandingPage.tsx": "pre-authentication marketing page, rendered outside <App />",
  "pages/LoginPage.tsx": "pre-authentication auth card, rendered outside <App />",
  "components/OnboardingGate.tsx": "fixed full-viewport agency chooser; its heading is the product name, not a page name",
};

/** Elements mounted inside another page, which must not add a second `<h1>`. */
const NESTED = [
  "pages/admin/AdminLayout.tsx",
  "pages/admin/AdminUserDetailPage.tsx",
];

/** Comments describe the rule; only real markup counts against it. */
function read(relative: string): string {
  return readFileSync(join(SRC, relative), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^[ \t]*\/\/.*$/gm, "");
}

function count(source: string, needle: string): number {
  return source.split(needle).length - 1;
}

describe("page shell", () => {
  test.each(SHELL_PAGES)("%s titles itself through exactly one PageHeader", (relative) => {
    const source = read(relative);
    expect(count(source, "<PageHeader")).toBe(1);
    expect(count(source, "<h1")).toBe(0);
  });

  test.each(Object.entries(OWN_TITLE))("%s keeps its own heading (%s)", (relative) => {
    const source = read(relative);
    expect(count(source, "<h1")).toBeGreaterThan(0);
    expect(count(source, "<PageHeader")).toBe(0);
  });

  test.each(NESTED)("%s adds no page title of its own", (relative) => {
    const source = read(relative);
    expect(count(source, "<h1")).toBe(0);
    expect(count(source, "<PageHeader")).toBe(0);
  });
});
