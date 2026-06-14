import { describe, it, expect, vi } from "vitest";
import { makeMockMap } from "../../test/mockMap";
import { whenStyleReady } from "./styleReady";

describe("whenStyleReady", () => {
  it("runs fn synchronously when the style is already loaded", () => {
    const map = makeMockMap([{ id: "basemap" }], true);
    const fn = vi.fn();
    whenStyleReady(map as never, fn);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("defers until a styledata reports the style ready", () => {
    const map = makeMockMap([{ id: "basemap" }], false);
    const fn = vi.fn();
    whenStyleReady(map as never, fn);
    expect(fn).not.toHaveBeenCalled();
    map.fire("styledata"); // not ready yet → still nothing
    expect(fn).not.toHaveBeenCalled();
    map.settleStyle(); // ready + styledata → fires
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("fires via the idle backstop when no qualifying styledata arrives", () => {
    const map = makeMockMap([{ id: "basemap" }], false);
    const fn = vi.fn();
    whenStyleReady(map as never, fn);
    map.settleViaIdle(); // only `idle` (the raster-tile case)
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("fires fn exactly once when both styledata and idle arrive", () => {
    const map = makeMockMap([{ id: "basemap" }], false);
    const fn = vi.fn();
    whenStyleReady(map as never, fn);
    map.settleStyle(); // styledata (ready) → fires once + detaches
    map.fire("idle"); // must NOT fire again
    map.fire("styledata");
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("cleanup detaches listeners so fn never fires after the effect re-runs", () => {
    const map = makeMockMap([{ id: "basemap" }], false);
    const fn = vi.fn();
    const cleanup = whenStyleReady(map as never, fn);
    cleanup();
    map.settleStyle();
    map.fire("idle");
    expect(fn).not.toHaveBeenCalled();
  });
});
