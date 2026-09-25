import { describe, it, expect } from "vitest";
import { modifierKeyLabel } from "./platform";

describe("modifierKeyLabel", () => {
  it("returns the command symbol on macOS (userAgentData)", () => {
    expect(modifierKeyLabel({ userAgentData: { platform: "macOS" } })).toBe("⌘");
  });

  it("returns the command symbol on the deprecated platform string (Mac/iPhone/iPad)", () => {
    expect(modifierKeyLabel({ platform: "MacIntel" })).toBe("⌘");
    expect(modifierKeyLabel({ platform: "iPhone" })).toBe("⌘");
    expect(modifierKeyLabel({ platform: "iPad" })).toBe("⌘");
  });

  it("returns Ctrl on Windows/Linux", () => {
    expect(modifierKeyLabel({ userAgentData: { platform: "Windows" } })).toBe("Ctrl");
    expect(modifierKeyLabel({ platform: "Linux x86_64" })).toBe("Ctrl");
  });

  it("prefers userAgentData.platform over the deprecated platform string when both are present", () => {
    expect(modifierKeyLabel({ userAgentData: { platform: "Windows" }, platform: "MacIntel" })).toBe("Ctrl");
  });

  it("falls back to Ctrl when neither source reports a platform", () => {
    expect(modifierKeyLabel({})).toBe("Ctrl");
  });
});
