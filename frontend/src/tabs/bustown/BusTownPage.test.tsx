import { describe, it, expect, vi, afterEach } from "vitest";
import { act, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route, Link } from "react-router-dom";
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
  departures_by_hour: [],
  stops: { id: ["s1", "s2"], lat: [35.0, 35.1], lon: [139.0, 139.1] },
  routes: [{ code: "R1", name: "Red Line", color: null }],
  trips: [{ trip_id: "t1", route_code: "R1", stop_idx: [0, 1], t: [4 * 3600, 4 * 3600 + 600], delay: [30, 400] }],
};

function nonEmptyReplay(overrides: Partial<BusTownReplay> = {}): BusTownReplay {
  const trips = mergeReplayWindows([SAMPLE_WINDOW]);
  return {
    loading: false,
    error: null,
    loaded: true,
    date: "2026-01-05",
    availableDates: ["2026-01-05", "2026-01-04"],
    departuresByHour: [
      { hour: 4, departures: 2 },
      { hour: 5, departures: 5 },
      { hour: 6, departures: 1 },
    ],
    originHour: 4,
    minSec: 4 * 3600,
    maxSec: 28 * 3600,
    playhead: 4 * 3600,
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
    loaded: true,
    date: "2026-01-05",
    availableDates: ["2026-01-05"],
    departuresByHour: [],
    originHour: 4,
    minSec: 4 * 3600,
    maxSec: 28 * 3600,
    playhead: 4 * 3600,
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
    playing: false,
    play: vi.fn(),
    pause: vi.fn(),
    seek: vi.fn(),
    canvasRef: vi.fn(),
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
    expect(lastCall[0].speed).toBe(360);
  });

  it("seeks when the scrubber moves", () => {
    const { playback } = setup(nonEmptyReplay());
    renderBusTown();
    fireEvent.change(screen.getByRole("slider", { name: /scrub/i }), { target: { value: String(5 * 3600) } });
    expect(playback.seek).toHaveBeenCalledWith(5 * 3600);
  });

  it("gives the scrubber a spoken value matching the hidden clock overlay", () => {
    setup(nonEmptyReplay());
    renderBusTown();
    expect(screen.getByRole("slider", { name: /scrub/i })).toHaveAttribute("aria-valuetext", "04:00");
  });

  it("floats the clock readout inside the canvas's own frame", () => {
    setup(nonEmptyReplay());
    renderBusTown();
    expect(document.querySelector(".bus-town-canvas-frame .bus-town-clock")).toBeInTheDocument();
    expect(document.querySelector(".bus-town-canvas-frame .bus-town-clock")).toHaveTextContent("04:00");
  });

  it("shows the busiest-hour strip under the scrubber once the day's hourly totals arrive", () => {
    setup(nonEmptyReplay());
    renderBusTown();
    expect(screen.getByText("Busiest hour: 05:00")).toBeInTheDocument();
  });

  it("shows no hour strip for a day with no hourly aggregate yet", () => {
    setup(nonEmptyReplay({ departuresByHour: [] }));
    renderBusTown();
    expect(screen.queryByText(/Busiest hour/)).not.toBeInTheDocument();
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
    setup(emptyReplay({ date: null, error: new Error("boom"), loaded: false }));
    renderBusTown();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("opens paused under reduced motion, but play still works", () => {
    const { playbackSpy } = setup(nonEmptyReplay(), mockPlayback({ playing: false }), true);
    renderBusTown();
    const lastCall = playbackSpy.mock.calls.at(-1)!;
    expect(lastCall[0].autoplay).toBe(false);
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
  });

  it("shows a note that positions between stops are estimated", () => {
    setup(nonEmptyReplay());
    renderBusTown();
    expect(screen.getByText("Positions between stops are estimated.")).toBeInTheDocument();
  });

  it("shows the day picker in the empty state so another day can still be chosen", () => {
    setup(emptyReplay());
    renderBusTown();
    expect(screen.getByText("No buses ran that we can replay")).toBeInTheDocument();
    expect(screen.getByLabelText("Day")).toBeInTheDocument();
  });

  it("shows the day picker in the error state so another day can still be chosen", () => {
    setup(emptyReplay({ date: null, error: new Error("boom"), loaded: false }));
    renderBusTown();
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByLabelText("Day")).toBeInTheDocument();
  });

  it("passes the resolved day down as the playback loop's restart key", () => {
    const { playbackSpy } = setup(nonEmptyReplay({ date: "2026-01-05" }));
    renderBusTown();
    expect(playbackSpy.mock.calls.at(-1)![0].dayKey).toBe("2026-01-05");
  });

  it("resets the playhead to null when another day is picked, so it re-resolves to that day's own first departure", () => {
    const { playbackSpy } = setup(nonEmptyReplay());
    const replaySpy = vi.mocked(replayModule.useBusTownReplay);
    renderBusTown();

    // Stand in for playback having advanced the playhead away from null,
    // the way the real playback loop's own `setSec` calls would.
    const setSec = playbackSpy.mock.calls.at(-1)![0].setSec;
    act(() => setSec(6 * 3600));
    expect(replaySpy.mock.calls.at(-1)![2]).toBe(6 * 3600);

    fireEvent.change(screen.getByLabelText("Day"), { target: { value: "2026-01-04" } });

    const lastCall = replaySpy.mock.calls.at(-1)!;
    expect(lastCall[1]).toBe("2026-01-04");
    expect(lastCall[2]).toBeNull();
  });

  it("resets the picked date when the agency switches", () => {
    setup(nonEmptyReplay());
    const replaySpy = vi.mocked(replayModule.useBusTownReplay);

    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/8/bus-town"]}>
        <Routes>
          <Route
            path="/agencies/:agencyId/bus-town"
            element={
              <>
                <Link to="/agencies/9/bus-town">switch agency</Link>
                <BusTownPage />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText("Day"), { target: { value: "2026-01-04" } });
    expect(replaySpy.mock.calls.at(-1)![0]).toBe(8);
    expect(replaySpy.mock.calls.at(-1)![1]).toBe("2026-01-04");

    fireEvent.click(screen.getByText("switch agency"));
    expect(replaySpy.mock.calls.at(-1)![0]).toBe(9);
    expect(replaySpy.mock.calls.at(-1)![1]).toBeNull();
  });
});
