import { describe, expect, it, vi } from "vitest";

const setWorkerUrl = vi.hoisted(() => vi.fn());
vi.mock("maplibre-gl", () => ({ setWorkerUrl, Map: class {} }));

describe("createSafeMap", () => {
  it("hands MapLibre the worker file the build emits", async () => {
    await import("./createSafeMap");
    expect(setWorkerUrl).toHaveBeenCalledWith(expect.stringMatching(/maplibre-gl-worker\.mjs$/));
  });
});
