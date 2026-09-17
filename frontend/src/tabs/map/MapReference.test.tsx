import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { MapReference } from "./MapReference";
import { readMapReferencePref } from "./mapReferencePref";

const t = ((key: string, opts?: Record<string, unknown>) =>
  key === "operations.map.disclosure"
    ? `${opts?.located} of ${opts?.total} trips · last reported stop`
    : key) as unknown as Parameters<typeof MapReference>[0]["t"];

function renderRef(located = 12, total = 14) {
  return render(<MapReference located={located} total={total} t={t} />);
}

describe("MapReference", () => {
  beforeEach(() => localStorage.clear());

  it("shows the legend and the marker note together by default", () => {
    renderRef();
    expect(screen.getByText("operations.map.legend_current")).toBeTruthy();
    expect(screen.getByText(/12 of 14 trips/)).toBeTruthy();
  });

  it("collapses to a single control and restores from it", async () => {
    renderRef();

    await userEvent.click(screen.getByRole("button", { name: "operations.map.reference_hide" }));
    expect(screen.queryByText(/12 of 14 trips/)).toBeNull();
    expect(screen.queryByText("operations.map.legend_current")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "operations.map.reference_show" }));
    expect(screen.getByText(/12 of 14 trips/)).toBeTruthy();
  });

  it("remembers being hidden across mounts", async () => {
    const first = renderRef();
    await userEvent.click(screen.getByRole("button", { name: "operations.map.reference_hide" }));
    first.unmount();

    renderRef();
    // An operator who dismissed this watches the screen for hours; re-showing
    // it on every remount would make the control feel like it did nothing.
    expect(screen.queryByText(/12 of 14 trips/)).toBeNull();
    expect(screen.getByRole("button", { name: "operations.map.reference_show" })).toBeTruthy();
  });

  it("defaults to shown when no preference is stored", () => {
    // The GPS caveat has to reach a first-time viewer before they read
    // anything into the marker positions.
    expect(readMapReferencePref()).toBe(true);
  });
});
