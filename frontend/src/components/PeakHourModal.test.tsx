import { describe, it, expect, vi, beforeAll, afterAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import i18n from "../i18n";
import { PeakHourModal } from "./PeakHourModal";
import type { PeakHourBreakdown } from "../api/types";

const mockBreakdown: PeakHourBreakdown = {
  hour: 8,
  dow: 5,
  routes: [
    { route_code: "K31", service_type: "平日", avg_min: 6.5, samples: 50 },
    { route_code: "K37", service_type: "平日", avg_min: 5.2, samples: 30 },
  ],
};

describe("PeakHourModal", () => {
  it("renders route list", () => {
    render(
      <PeakHourModal
        data={mockBreakdown}
        loading={false}
        onClose={() => {}}
      />
    );
    expect(screen.getByText("K31")).toBeInTheDocument();
    expect(screen.getByText("K37")).toBeInTheDocument();
  });

  it("calls onClose when backdrop clicked", () => {
    const onClose = vi.fn();
    render(
      <PeakHourModal data={mockBreakdown} loading={false} onClose={onClose} />
    );
    fireEvent.click(screen.getByTestId("peak-hour-modal-backdrop"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("shows empty state when no routes", () => {
    const empty: PeakHourBreakdown = { hour: 3, dow: null, routes: [] };
    render(<PeakHourModal data={empty} loading={false} onClose={() => {}} />);
    expect(screen.getByTestId("peak-hour-modal-empty")).toBeInTheDocument();
  });

  it("shows spinner when loading", () => {
    render(<PeakHourModal data={null} loading={true} onClose={() => {}} />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("clamps the bar width to 0 instead of a negative value for an early-running route", () => {
    // maxAvg is set by K31 (a delayed route); K37 is early-running (negative
    // avg_min), which used to divide out to a negative CSS width percentage.
    const mixed: PeakHourBreakdown = {
      hour: 8,
      dow: 5,
      routes: [
        { route_code: "K31", service_type: "平日", avg_min: 6.5, samples: 50 },
        { route_code: "K37", service_type: "平日", avg_min: -3.2, samples: 30 },
      ],
    };
    render(<PeakHourModal data={mixed} loading={false} onClose={() => {}} />);
    const bar = screen.getByTestId("peak-hour-modal-bar-K37");
    expect(bar.style.width).toBe("0%");
    // The label still shows the true negative value.
    expect(screen.getByText(/-3\.2/)).toBeInTheDocument();
  });

  describe("title grammar in Japanese", () => {
    beforeAll(async () => await i18n.changeLanguage("ja"));
    afterAll(async () => await i18n.changeLanguage("en"));

    it("does not render the broken '全曜' when dow is null (the ribbon's every-hour-click path)", () => {
      const allDays: PeakHourBreakdown = { hour: 10, dow: null, routes: mockBreakdown.routes };
      render(<PeakHourModal data={allDays} loading={false} onClose={() => {}} />);
      expect(screen.queryByText(/全曜/)).toBeNull();
    });

    it("renders a specific day's title correctly", () => {
      render(<PeakHourModal data={mockBreakdown} loading={false} onClose={() => {}} />);
      expect(screen.getByText(/金曜 8時台/)).toBeInTheDocument();
    });
  });
});
