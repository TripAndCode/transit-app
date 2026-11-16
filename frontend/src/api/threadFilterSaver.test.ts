import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cancelThreadFilter,
  flushThreadFilter,
  pendingThreadFilter,
  resetThreadFilterSaves,
  saveThreadFilter,
} from "./threadFilterSaver";

function deferredWrite() {
  const calls: { filter: unknown; resolve: () => void; reject: (e: unknown) => void }[] = [];
  const write = vi.fn(
    (filter: unknown) =>
      new Promise<void>((resolve, reject) => {
        calls.push({ filter, resolve, reject });
      }),
  );
  return { write, calls };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("threadFilterSaver", () => {
  afterEach(() => resetThreadFilterSaves());

  it("sends a burst of edits as at most two writes, in order, ending with the latest", async () => {
    const { write, calls } = deferredWrite();
    saveThreadFilter("t1", { dow: "weekday" }, write);
    saveThreadFilter("t1", { dow: "weekend" }, write);
    saveThreadFilter("t1", { dow: "mon" }, write);
    expect(write).toHaveBeenCalledTimes(1);
    expect(pendingThreadFilter("t1")).toEqual({ dow: "mon" });

    calls[0].resolve();
    await tick();
    expect(write).toHaveBeenCalledTimes(2);
    expect(calls.map((c) => c.filter)).toEqual([{ dow: "weekday" }, { dow: "mon" }]);

    let flushed = false;
    void flushThreadFilter("t1").then(() => (flushed = true));
    await tick();
    expect(flushed).toBe(false);
    calls[1].resolve();
    await tick();
    expect(flushed).toBe(true);
    expect(pendingThreadFilter("t1")).toBeNull();
  });

  it("rejects a flush after a failed save, and sends that save again on the next flush", async () => {
    const write = vi.fn().mockRejectedValueOnce(new Error("save failed")).mockResolvedValue(undefined);
    saveThreadFilter("t1", { dow: "weekday" }, write);
    await expect(flushThreadFilter("t1")).rejects.toThrow("save failed");
    expect(pendingThreadFilter("t1")).toEqual({ dow: "weekday" });

    await expect(flushThreadFilter("t1")).resolves.toBeUndefined();
    expect(write).toHaveBeenCalledTimes(2);
    expect(write.mock.calls[1][0]).toEqual({ dow: "weekday" });
  });

  it("drops a deleted thread's queued save and ignores its running one's failure", async () => {
    const { write, calls } = deferredWrite();
    const onIdle = vi.fn();
    saveThreadFilter("t1", { dow: "weekday" }, write, onIdle);
    saveThreadFilter("t1", { dow: "weekend" }, write);
    cancelThreadFilter("t1");
    calls[0].reject(new Error("404"));
    await tick();
    expect(write).toHaveBeenCalledTimes(1);
    await expect(flushThreadFilter("t1")).resolves.toBeUndefined();
    expect(onIdle).not.toHaveBeenCalled();
  });

  it("calls onIdle once the last save lands with nothing queued", async () => {
    const { write, calls } = deferredWrite();
    const onIdle = vi.fn();
    saveThreadFilter("t1", { dow: "weekday" }, write, onIdle);
    saveThreadFilter("t1", { dow: "weekend" }, write, onIdle);
    calls[0].resolve();
    await tick();
    expect(onIdle).not.toHaveBeenCalled();
    calls[1].resolve();
    await tick();
    expect(onIdle).toHaveBeenCalledTimes(1);
  });
});
