import { describe, it, expect, vi } from "vitest";
import { useEffect, useState } from "react";
import { act, render, screen } from "@testing-library/react";
import { getConfig } from "@testing-library/dom";
import { ASYNC_UTIL_TIMEOUT_MS } from "./timeouts";

/** Shows its text after `delayMs`, standing in for a mocked query that a
 *  loaded machine answers late. */
function Late({ delayMs }: { delayMs: number }) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setShown(true), delayMs);
    return () => clearTimeout(timer);
  }, [delayMs]);
  return shown ? <p>arrived</p> : null;
}

describe("Testing Library's async queries", () => {
  it("wait longer than their 1 s default before giving up", () => {
    expect(getConfig().asyncUtilTimeout).toBe(ASYNC_UTIL_TIMEOUT_MS);
    expect(ASYNC_UTIL_TIMEOUT_MS).toBeGreaterThan(1000);
  });

  // Fake timers run the wait's own deadline and the late render on one
  // clock: with a 1 s deadline the wait gives up before the text arrives.
  // The clock also follows real time, for the zero-delay timer the wait
  // settles on once it has found its element.
  it("still find content that a busy machine renders after more than a second", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<Late delayMs={1500} />);
      const found = screen.findByText("arrived");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      expect(await found).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
