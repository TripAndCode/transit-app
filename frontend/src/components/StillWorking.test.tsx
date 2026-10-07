import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { SCOPE_EXTRAS_NONE, type Scope } from "../api/scope";
import { STILL_WORKING_AFTER_MS, StillWorking } from "./StillWorking";

function scope(from: string, to: string): Scope {
  return { ...SCOPE_EXTRAS_NONE, from, to, dow: "all", time_band: "all", service: "all", routes: [] };
}

describe("StillWorking", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("says nothing while a fetch is quick, then that it is still working", () => {
    renderWithProviders(<StillWorking />);
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
    act(() => vi.advanceTimersByTime(STILL_WORKING_AFTER_MS - 1));
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByRole("status")).toHaveTextContent("Still working on it…");
  });

  it("offers the period's last week when the period is longer", () => {
    const update = vi.fn();
    renderWithProviders(<StillWorking scope={scope("2026-09-01", "2026-09-30")} update={update} />);
    act(() => vi.advanceTimersByTime(STILL_WORKING_AFTER_MS));
    expect(screen.getByRole("status")).toHaveTextContent("Still working: a long period takes longer to count.");
    fireEvent.click(screen.getByRole("button", { name: "Narrow to the last 7 days of the period" }));
    expect(update).toHaveBeenCalledWith({ from: "2026-09-24" });
  });

  it("offers nothing narrower for a week or less", () => {
    renderWithProviders(<StillWorking scope={scope("2026-09-24", "2026-09-30")} update={vi.fn()} />);
    act(() => vi.advanceTimersByTime(STILL_WORKING_AFTER_MS));
    expect(screen.getByRole("status")).toHaveTextContent("Still working on it…");
    expect(screen.queryByRole("button")).toBeNull();
  });
});
