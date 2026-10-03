import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "../../i18n";
import { StopRibbon } from "./StopRibbon";
import type { StopRibbonSegment } from "./mareyLayout";

const SEGMENTS: StopRibbonSegment[] = [
  { stop_sequence: 1, stop_name: "Station", delay_sec: 0, samples: 4 },
  { stop_sequence: 2, stop_name: "Centre", delay_sec: 120, samples: 4 },
  { stop_sequence: 3, stop_name: "Terminus", delay_sec: 660, samples: 4 },
  { stop_sequence: 4, stop_name: "Depot", delay_sec: null, samples: 0 },
];

describe("StopRibbon", () => {
  it("draws one band per stop, in sequence order", () => {
    const { container } = render(<StopRibbon segments={SEGMENTS} label="Delay by stop" />);
    const bands = [...container.querySelectorAll("[data-stop-sequence]")];
    expect(bands.map((b) => b.getAttribute("data-stop-sequence"))).toEqual(["1", "2", "3", "4"]);
  });

  it("fills each band from the shared delay ramp", () => {
    const { container } = render(<StopRibbon segments={SEGMENTS} label="Delay by stop" />);
    const bands = [...container.querySelectorAll("[data-stop-sequence]")];
    expect(bands[0].getAttribute("fill")).toBe("#2EA87A");
    expect(bands[1].getAttribute("fill")).toBe("#C99A2E");
    expect(bands[2].getAttribute("fill")).toBe("var(--delay-severe)");
  });

  it("shows an unobserved stop as track, not as on time", () => {
    const { container } = render(<StopRibbon segments={SEGMENTS} label="Delay by stop" />);
    const bands = [...container.querySelectorAll("[data-stop-sequence]")];
    expect(bands[3].getAttribute("fill")).toBe("var(--track-bg)");
  });

  it("names itself and each band for a screen reader", () => {
    render(<StopRibbon segments={SEGMENTS} label="Delay by stop" />);
    expect(screen.getByRole("img", { name: "Delay by stop" })).toBeInTheDocument();
    expect(screen.getByText("Centre: 2.0 min")).toBeInTheDocument();
    expect(screen.getByText("Depot: No observation")).toBeInTheDocument();
  });

  it("is inert by default and interactive only when a handler is given", () => {
    const { container, rerender } = render(<StopRibbon segments={SEGMENTS} label="Delay by stop" />);
    expect(container.querySelector("[role='button']")).toBeNull();

    const onSelect = vi.fn();
    rerender(<StopRibbon segments={SEGMENTS} label="Delay by stop" onSelect={onSelect} />);
    const bands = [...container.querySelectorAll("[data-stop-sequence]")];
    expect(bands.every((b) => b.getAttribute("role") === "button")).toBe(true);
    fireEvent.click(bands[1]);
    expect(onSelect).toHaveBeenCalledWith(2);
  });

  it("selects a band from the keyboard when it is interactive", () => {
    const onSelect = vi.fn();
    const { container } = render(<StopRibbon segments={SEGMENTS} label="Delay by stop" onSelect={onSelect} />);
    fireEvent.keyDown(container.querySelectorAll("[data-stop-sequence]")[2], { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith(3);
  });

  it("marks the selected stop", () => {
    const { container } = render(<StopRibbon segments={SEGMENTS} label="Delay by stop" selectedSequence={2} />);
    const bands = [...container.querySelectorAll("[data-stop-sequence]")];
    expect(bands[1].getAttribute("data-selected")).toBe("true");
    expect(bands[0].getAttribute("data-selected")).toBe("false");
  });

  it("renders nothing for an empty route", () => {
    const { container } = render(<StopRibbon segments={[]} label="Delay by stop" />);
    expect(container.querySelector("svg")).toBeNull();
  });
});
