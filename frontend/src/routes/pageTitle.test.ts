import { describe, it, expect } from "vitest";
import { pageTitleKey } from "./pageTitle";

describe("pageTitleKey", () => {
  it.each([
    ["/agencies/3/pulse", "nav.pulse"],
    ["/agencies/3/routes", "nav.routes"],
    ["/agencies/3/routes/39061", "nav.routes"],
    ["/agencies/3/time", "nav.time"],
    ["/agencies/3/ask", "nav.ask"],
    ["/me", "account.title"],
    ["/help", "nav.help"],
    ["/admin/flags", "account.admin_link"],
  ])("names %s by its screen", (path, key) => {
    expect(pageTitleKey(path)).toBe(key);
  });

  it("names nothing for a path that isn't a screen", () => {
    expect(pageTitleKey("/")).toBeNull();
    expect(pageTitleKey("/agencies/3/nonsense")).toBeNull();
  });
});
