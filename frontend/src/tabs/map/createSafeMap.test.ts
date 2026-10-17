import { describe, expect, it, vi } from "vitest";

const setWorkerUrl = vi.hoisted(() => vi.fn());
vi.mock("maplibre-gl", () => ({ setWorkerUrl, Map: class {} }));

describe("createSafeMap", () => {
  it("hands MapLibre the URL of its worker module on import", async () => {
    await import("./createSafeMap");
    expect(setWorkerUrl).toHaveBeenCalledWith(expect.stringMatching(/maplibre-gl-worker\.mjs$/));
  });
});
