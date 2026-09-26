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
  const segment = COPILOT_INSIGHT_ROUTE.split("/").pop()!;

  it("renders on a route App does not treat as focused", () => {
    const path = COPILOT_INSIGHT_ROUTE.replace(":agencyId", "1");
    expect(FOCUSED_TAB_PATTERN.test(path)).toBe(false);
  });

  it("does not name a focused tab segment", () => {
    expect(FOCUSED_TAB_SEGMENTS).not.toContain(segment);
  });

  it("is still a real trailing segment, so the check above cannot pass vacuously", () => {
    expect(segment).toBe("period-overview");
    expect(FOCUSED_TAB_PATTERN.test("/agencies/1/operations")).toBe(true);
  });
});
