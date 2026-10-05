import { describe, it, expect } from "vitest";
import { useEffect, useState } from "react";
import { render, screen } from "@testing-library/react";
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

  it("still find content that a busy machine renders after more than a second", async () => {
    render(<Late delayMs={1500} />);
    expect(await screen.findByText("arrived")).toBeInTheDocument();
  });
});
