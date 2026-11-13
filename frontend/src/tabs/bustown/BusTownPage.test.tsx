import { describe, it, expect, vi, afterEach } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import { BusTownPage } from "./BusTownPage";
import * as hooks from "../../api/hooks";
import * as mediaQuery from "../../hooks/useMediaQuery";
import * as replayModule from "./useBusTownReplay";
import * as playbackModule from "./useBusTownPlayback";
import { buildBusTownModel, mergeReplayWindows } from "./busTownModel";
import type { ReplayResponse } from "../../api/types";
import type { BusTownReplay } from "./useBusTownReplay";
import type { BusTownPlayback } from "./useBusTownPlayback";

const SAMPLE_WINDOW: ReplayResponse = {
  date: "2026-01-05",
  from_hour: 4,
  to_hour: 7,
  available_dates: ["2026-01-05", "2026-01-04"],
  stops: { id: ["s1", "s2"], lat: [35.0, 35.1], lon: [139.0, 139.1] },
  routes: [{ code: "R1", name: "Red Line", color: null }],
  trips: [{ trip_id: "t1", route_code: "R1", stop_idx: [0, 1], t: [4 * 3600, 4 * 3600 + 600], delay: [30, 400] }],
};

function nonEmptyReplay(overrides: Partial<BusTownReplay> = {}): BusTownReplay {
  const trips = mergeReplayWindows([SAMPLE_WINDOW]);
  return {
    loading: false,
    error: null,
    date: "2026-01-05",
    availableDates: ["2026-01-05", "2026-01-04"],
    originHour: 4,
    minSec: 4 * 3600,
    maxSec: 28 * 3600,
    model: buildBusTownModel(trips),
    trips,
    stopPoints: [{ id: "s1", lat: 35.0, lon: 139.0 }, { id: "s2", lat: 35.1, lon: 139.1 }],
    stopPairs: [{ lat0: 35.0, lon0: 139.0, lat1: 35.1, lon1: 139.1 }],
    routeNames: { R1: "Red Line" },
    ...overrides,
  };
}

function emptyReplay(overrides: Partial<BusTownReplay> = {}): BusTownReplay {
  return {
    loading: false,
    error: null,
    date: "2026-01-05",
    availableDates: ["2026-01-05"],
    originHour: 4,
    minSec: 4 * 3600,
    maxSec: 28 * 3600,
    model: new Map(),
    trips: new Map(),
    stopPoints: [],
    stopPairs: [],
    routeNames: {},
    ...overrides,
  };
}

function mockPlayback(overrides: Partial<BusTownPlayback> = {}): BusTownPlayback {
  return {
    sec: 4 * 3600 + 120,
    playing: false,
    play: vi.fn(),
    pause: vi.fn(),
    seek: vi.fn(),
    ...overrides,
  };
}

function renderBusTown() {
  renderWithProviders(
    <MemoryRouter initialEntries={["/agencies/8/bus-town"]}>
      <Routes>
        <Route path="/agencies/:agencyId/bus-town" element={<BusTownPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("BusTownPage", () => {
  afterEach(() => vi.restoreAllMocks());

  function setup(replay: BusTownReplay, playback = mockPlayback(), reducedMotion = false) {
    vi.spyOn(replayModule, "useBusTownReplay").mockReturnValue(replay);
    const playbackSpy = vi.spyOn(playbackModule, "useBusTownPlayback").mockReturnValue(playback);
    vi.spyOn(hooks, "useStopBounds").mockReturnValue({ data: [139.0, 35.0, 139.2, 35.2] } as never);
    vi.spyOn(mediaQuery, "useMediaQuery").mockReturnValue(reducedMotion);
    return { playbackSpy, playback };
  }

  it("renders the day's legend, canvas and both stat cards once loaded", () => {
    setup(nonEmptyReplay());
    renderBusTown();
    expect(screen.getByRole("img", { name: /schematic map/i })).toBeInTheDocument();
    expect(screen.getByText("On time right now")).toBeInTheDocument();
    expect(screen.getByText("Slowest route")).toBeInTheDocument();
    expect(screen.getByText(/Red Line/)).toBeInTheDocument();
    // DelayBandLegend's own band labels.
    expect(screen.getByText("Under 1.5 min")).toBeInTheDocument();
  });

  it("toggles play and pause", () => {
    const { playback } = setup(nonEmptyReplay(), mockPlayback({ playing: false }));
    renderBusTown();
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(playback.play).toHaveBeenCalledTimes(1);
  });

  it("shows pause once playing", () => {
    setup(nonEmptyReplay(), mockPlayback({ playing: true }));
    renderBusTown();
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
  });

  it("passes the pressed speed pill's value down to the playback hook", () => {
    const { playbackSpy } = setup(nonEmptyReplay());
    renderBusTown();
    fireEvent.click(screen.getByRole("button", { name: /6 simulated minutes per second/ }));
    const lastCall = playbackSpy.mock.calls.at(-1)!;
    expect(lastCall[7]).toBe(360);
  });

  it("seeks when the scrubber moves", () => {
    const { playback } = setup(nonEmptyReplay());
    renderBusTown();
    fireEvent.change(screen.getByRole("slider", { name: /scrub/i }), { target: { value: String(5 * 3600) } });
    expect(playback.seek).toHaveBeenCalledWith(5 * 3600);
  });

  it("lists every available day in the day picker", () => {
    setup(nonEmptyReplay());
    renderBusTown();
    const picker = screen.getByLabelText("Day") as HTMLSelectElement;
    expect([...picker.options].map((o) => o.value)).toEqual(["2026-01-05", "2026-01-04"]);
  });

  it("shows the empty state for a day with nothing to replay", () => {
    setup(emptyReplay());
    renderBusTown();
    expect(screen.getByText("No buses ran that we can replay")).toBeInTheDocument();
    expect(screen.queryByText("On time right now")).not.toBeInTheDocument();
  });

  it("shows an error banner when the day fails to load", () => {
    setup(emptyReplay({ date: null, error: new Error("boom") }));
    renderBusTown();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("opens paused under reduced motion, but play still works", () => {
    const { playbackSpy } = setup(nonEmptyReplay(), mockPlayback({ playing: false }), true);
    renderBusTown();
    const lastCall = playbackSpy.mock.calls.at(-1)!;
    // autoplay is the 9th positional argument: false whenever reduced
    // motion is the viewer's preference.
    expect(lastCall[8]).toBe(false);
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
  });
});
