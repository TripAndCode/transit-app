import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/renderWithProviders";
import { MapHourScrubber } from "./MapHourScrubber";

function setup(overrides: Partial<Parameters<typeof MapHourScrubber>[0]> = {}) {
  const onHourChange = vi.fn();
  const onTogglePlay = vi.fn();
  renderWithProviders(
    <MapHourScrubber
      hour={15}
      onHourChange={onHourChange}
      expectedDelayMin={3.2}
      playing={false}
      onTogglePlay={onTogglePlay}
      {...overrides}
    />,
  );
  return { onHourChange, onTogglePlay };
}

describe("MapHourScrubber", () => {
  it("renders the current hour and expected delay", () => {
    setup();
    expect(screen.getByText("15:00")).toBeInTheDocument();
    expect(screen.getByText("Expected delay 3.2 min")).toBeInTheDocument();
  });

  it("explains what the control shows, so it isn't mistaken for live vehicle tracking", () => {
    setup();
    expect(screen.getByText("Expected delay by time of day")).toBeInTheDocument();
    expect(
      screen.getByText("This route's typical congestion pattern (not live vehicle position)"),
    ).toBeInTheDocument();
  });

  it("shows the no-data message when expectedDelayMin is null", () => {
    setup({ expectedDelayMin: null });
    expect(screen.getByText("Not enough data for this hour")).toBeInTheDocument();
  });

  it("calls onHourChange when the slider moves", () => {
    const { onHourChange } = setup();
    const slider = screen.getByRole("slider", { name: "Time-of-day scrubber" }) as HTMLInputElement;
    // Controlled inputs need the native value setter, not a direct `.value =`
    // assignment: React wraps the DOM value setter to track the "last known"
    // value, and a raw assignment updates that tracker before the event
    // fires, so the following native "input" event is seen as a no-op change
    // and React's onChange never runs (see facebook/react#10135).
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!;
    nativeSetter.call(slider, "18");
    slider.dispatchEvent(new Event("input", { bubbles: true }));
    expect(onHourChange).toHaveBeenCalledWith(18);
  });

  it("shows Play when not playing and calls onTogglePlay when clicked", async () => {
    const user = userEvent.setup();
    const { onTogglePlay } = setup({ playing: false });
    await user.click(screen.getByRole("button", { name: "Play" }));
    expect(onTogglePlay).toHaveBeenCalledTimes(1);
  });

  it("shows Pause when playing", () => {
    setup({ playing: true });
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
  });
});
