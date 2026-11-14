import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import i18n from "../../i18n";
import type { ReplayHour } from "../../api/types";
import { BusTownHourStrip } from "./BusTownHourStrip";

const t = i18n.t.bind(i18n);

function bars(container: HTMLElement): HTMLElement[] {
  return [...container.querySelectorAll<HTMLElement>(".bus-town-hour-bar")];
}

describe("BusTownHourStrip", () => {
  it("renders one bar per hour across the default 24-hour range", () => {
    const hours: ReplayHour[] = [{ hour: 4, departures: 3 }];
    const { container } = render(
      <BusTownHourStrip hours={hours} startHour={4} endHour={28} playheadSec={4 * 3600} t={t} />,
    );
    expect(bars(container)).toHaveLength(24);
  });

  it("renders more bars when the day's own service starts before 04:00", () => {
    const hours: ReplayHour[] = [{ hour: 5, departures: 1 }];
    const { container } = render(
      <BusTownHourStrip hours={hours} startHour={2} endHour={28} playheadSec={2 * 3600} t={t} />,
    );
    expect(bars(container)).toHaveLength(26);
  });

  it("marks the playhead's own hour as current", () => {
    const hours: ReplayHour[] = [
      { hour: 4, departures: 2 },
      { hour: 5, departures: 4 },
    ];
    const { container } = render(
      <BusTownHourStrip hours={hours} startHour={4} endHour={8} playheadSec={5 * 3600 + 30} t={t} />,
    );
    const all = bars(container);
    const current = all.filter((b) => b.classList.contains("is-current"));
    expect(current).toHaveLength(1);
    expect(current[0].style.height).toBe("100%");
  });

  it("marks no bar as current at the range's last instant", () => {
    const hours: ReplayHour[] = [
      { hour: 4, departures: 2 },
      { hour: 5, departures: 4 },
    ];
    const { container } = render(
      <BusTownHourStrip hours={hours} startHour={4} endHour={6} playheadSec={6 * 3600} t={t} />,
    );
    expect(bars(container).filter((b) => b.classList.contains("is-current"))).toHaveLength(0);
  });

  it("scales every bar's height relative to the busiest hour in range, with that bar at 100%", () => {
    const hours: ReplayHour[] = [
      { hour: 4, departures: 10 },
      { hour: 5, departures: 5 },
    ];
    const { container } = render(
      <BusTownHourStrip hours={hours} startHour={4} endHour={6} playheadSec={4 * 3600} t={t} />,
    );
    const all = bars(container);
    expect(all[0].style.height).toBe("100%");
    expect(all[1].style.height).toBe("50%");
  });

  it("keeps a zero or missing hour's slot without giving it a bar height", () => {
    const hours: ReplayHour[] = [{ hour: 4, departures: 6 }];
    const { container } = render(
      <BusTownHourStrip hours={hours} startHour={4} endHour={6} playheadSec={4 * 3600} t={t} />,
    );
    const all = bars(container);
    expect(all).toHaveLength(2);
    expect(all[1].classList.contains("has-value")).toBe(false);
    expect(all[1].style.height).toBe("");
  });

  it("renders nothing at all when no hour in range has any departures", () => {
    const hours: ReplayHour[] = [{ hour: 20, departures: 5 }];
    const { container } = render(
      <BusTownHourStrip hours={hours} startHour={4} endHour={8} playheadSec={4 * 3600} t={t} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the day has no hourly aggregate at all", () => {
    const { container } = render(
      <BusTownHourStrip hours={[]} startHour={4} endHour={28} playheadSec={4 * 3600} t={t} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("names the busiest hour in the visible caption", () => {
    const hours: ReplayHour[] = [
      { hour: 4, departures: 2 },
      { hour: 8, departures: 9 },
      { hour: 17, departures: 3 },
    ];
    const { getByText } = render(
      <BusTownHourStrip hours={hours} startHour={4} endHour={28} playheadSec={4 * 3600} t={t} />,
    );
    expect(getByText("Busiest hour: 08:00")).toBeInTheDocument();
  });

  it("breaks a tie for busiest hour toward the earliest hour", () => {
    const hours: ReplayHour[] = [
      { hour: 4, departures: 5 },
      { hour: 9, departures: 5 },
    ];
    const { getByText } = render(
      <BusTownHourStrip hours={hours} startHour={4} endHour={28} playheadSec={4 * 3600} t={t} />,
    );
    expect(getByText("Busiest hour: 04:00")).toBeInTheDocument();
  });

  it("hides the bar row from the accessibility tree, leaving the caption as the text equivalent", () => {
    const hours: ReplayHour[] = [{ hour: 4, departures: 2 }];
    const { container } = render(
      <BusTownHourStrip hours={hours} startHour={4} endHour={6} playheadSec={4 * 3600} t={t} />,
    );
    expect(container.querySelector(".bus-town-hour-bars")?.getAttribute("aria-hidden")).toBe("true");
  });
});
