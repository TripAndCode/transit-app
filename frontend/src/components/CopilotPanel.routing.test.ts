import { describe, it, expect } from "vitest";
import { FOCUSED_TAB_SEGMENTS, FOCUSED_TAB_PATTERN } from "../routes/focusedTabs";
import { COPILOT_INSIGHT_ROUTE } from "./CopilotPanel";

/**
 * App renders `{!focused && <CopilotPanel />}`, and CopilotPanel returns null
 * unless the pathname matches COPILOT_INSIGHT_ROUTE. Those two conditions have
 * to be satisfiable at the same time or the panel is unreachable — mounted
 * nowhere it has content, and rendering nothing everywhere it is mounted.
 *
 * The component's own tests mount it directly on its route, bypassing App's
 * gate entirely, so they stay green either way. This asserts the relationship
 * between the two files instead.
 */
describe("CopilotPanel reachability", () => {
  const tail = COPILOT_INSIGHT_ROUTE.replace("/agencies/:agencyId/", "");

  it("renders on a route App does not treat as focused", () => {
    const path = COPILOT_INSIGHT_ROUTE.replace(":agencyId", "1");
    expect(FOCUSED_TAB_PATTERN.test(path)).toBe(false);
  });

  it("does not name a focused tab segment", () => {
    expect(FOCUSED_TAB_SEGMENTS).not.toContain(tail);
  });

  it("treats a trailing slash as the same focused tab, as the router's own matching does", () => {
    expect(FOCUSED_TAB_PATTERN.test("/agencies/1/ask/")).toBe(true);
    expect(FOCUSED_TAB_PATTERN.test("/agencies/1/live/")).toBe(true);
    expect(FOCUSED_TAB_PATTERN.test("/agencies/1/analysis/overview/")).toBe(false);
  });

  it("focuses the where lens and Saved & export but not the other lenses", () => {
    expect(FOCUSED_TAB_PATTERN.test("/agencies/1/analysis/where")).toBe(true);
    expect(FOCUSED_TAB_PATTERN.test("/agencies/1/saved")).toBe(true);
    expect(FOCUSED_TAB_PATTERN.test("/agencies/1/analysis/when")).toBe(false);
  });

  it("is still a real route, so the checks above cannot pass vacuously", () => {
    expect(COPILOT_INSIGHT_ROUTE).toBe("/agencies/:agencyId/analysis/overview");
    expect(FOCUSED_TAB_PATTERN.test("/agencies/1/live")).toBe(true);
  });
});
