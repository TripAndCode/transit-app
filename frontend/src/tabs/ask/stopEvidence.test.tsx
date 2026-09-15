import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import type { ConvMessage } from "../../api/types";
import { renderWithProviders } from "../../test/renderWithProviders";
import { stopEvidence } from "./stopEvidence";
import { StopEvidenceChart } from "./StopEvidenceChart";

const message = {
  role: "assistant", tool: "segment_hotspots",
  result: { kind: "table", columns: ["stop_sequence", "stop_name", "avg_min", "samples"], rows: [[7, "Central", 4.2, 128], [2, "Park", -1, 8]] },
} as ConvMessage;

describe("stop evidence", () => {
  it("preserves returned ranking, negative delays and observation counts", () => {
    expect(stopEvidence(message)).toEqual([
      { sequence: 7, name: "Central", minutes: 4.2, samples: 128 },
      { sequence: 2, name: "Park", minutes: -1, samples: 8 },
    ]);
  });
  it("does not interpret unrelated tables as stop observations", () => {
    expect(stopEvidence({ ...message, tool: "top_n" })).toBeNull();
    expect(stopEvidence({ ...message, result: { ...message.result!, columns: ["stop_name"] } })).toBeNull();
  });
  it.each([null, "4.2", Infinity, NaN])("rejects invalid measurements %s", (minutes) => {
    expect(stopEvidence({ ...message, result: { ...message.result!, rows: [[7, "Central", minutes, 8]] } })).toBeNull();
  });
  it("rejects duplicate sequence keys instead of selecting an ambiguous row", () => {
    expect(stopEvidence({ ...message, result: { ...message.result!, rows: [[7, "A", 2, 8], [7, "B", 3, 9]] } })).toBeNull();
  });
  it("selects an actual row locally and can clear it", () => {
    const onFocus = vi.fn();
    renderWithProviders(<StopEvidenceChart messageId={22} points={stopEvidence(message)!} onFocus={onFocus} />);
    expect(onFocus).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Central, sequence 7/ }));
    expect(onFocus).toHaveBeenCalledWith({ messageId: 22, sequence: 7, name: "Central" });
    expect(screen.getByText("128 observations")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(onFocus).toHaveBeenLastCalledWith(null);
  });
  it("opens only the selected evidence and dismisses with Escape without changing results", () => {
    renderWithProviders(<StopEvidenceChart messageId={22} points={stopEvidence(message)!} />);
    expect(screen.queryByRole("region", { name: "Selected stop evidence" })).not.toBeInTheDocument();
    const bar = screen.getByRole("button", { name: /Central, sequence 7/ });
    fireEvent.click(bar);
    expect(screen.getByRole("region", { name: "Selected stop evidence" })).toBeInTheDocument();
    fireEvent.keyDown(bar, { key: "Escape" });
    expect(screen.queryByRole("region", { name: "Selected stop evidence" })).not.toBeInTheDocument();
    expect(bar).toHaveFocus();
    expect(screen.getByRole("button", { name: /Park, sequence 2: -1 minutes/ })).toBeInTheDocument();
  });
  it("positions the detail popover from the selected bar's actual on-screen position, not a static fraction of the window", () => {
    renderWithProviders(<StopEvidenceChart messageId={22} points={stopEvidence(message)!} />);
    const bar = screen.getByRole("button", { name: /Central, sequence 7/ });
    const layout = document.querySelector(".stop-evidence-layout") as HTMLElement;
    // Simulate the strip having auto-scrolled so the selected bar sits at
    // screen x=340 while the layout container itself starts at x=100 --
    // a fraction of `selectedIndex / size` would not reproduce this.
    vi.spyOn(layout, "getBoundingClientRect").mockReturnValue({ left: 100 } as DOMRect);
    vi.spyOn(bar, "getBoundingClientRect").mockReturnValue({ left: 340 } as DOMRect);
    fireEvent.click(bar);
    const region = screen.getByRole("region", { name: "Selected stop evidence" });
    // jsdom's CSSOM mangles `clamp()`'s internal commas on both the property
    // and the attribute reflection, so assert on the computed pixel value
    // surviving that mangling rather than the exact (unreliable) string.
    expect(region.getAttribute("style")).toContain("240px");
  });
});
