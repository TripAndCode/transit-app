import { describe, it, expect } from "vitest";
import { WEEK } from "./week";

describe("WEEK", () => {
  it("is Monday-first, matching the pipeline's 1=Monday..7=Sunday dow convention", () => {
    expect(WEEK).toEqual(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]);
  });

  it("indexes correctly at dow - 1 for both ends of the week", () => {
    const dow = 1; // Monday
    expect(WEEK[dow - 1]).toBe("mon");
    const sunday = 7;
    expect(WEEK[sunday - 1]).toBe("sun");
  });
});
