import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import type { useAdminUsage } from "../../api/admin";
import { AdminUsagePage } from "./AdminUsagePage";

type UsageReturn = ReturnType<typeof useAdminUsage>;

let usageReturn: Partial<UsageReturn>;
let lastArgs: Parameters<typeof useAdminUsage>[0] | undefined;

vi.mock("../../api/admin", () => ({
  useAdminUsage: (params: Parameters<typeof useAdminUsage>[0]) => {
    lastArgs = params;
    return usageReturn;
  },
}));

function wrapAt(ui: React.ReactElement, path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

function wrap(ui: React.ReactElement) {
  return wrapAt(ui, "/admin/usage");
}

beforeEach(() => {
  usageReturn = {
    data: {
      from: "2026-10-17",
      to: "2026-11-15",
      routes: [
        {
          route: "/api/agencies",
          method: "GET",
          signed_out: 12,
          signed_in: 3,
          errors: 0,
        },
        {
          route: "/api/{agency_id}/overview/summary",
          method: "GET",
          signed_out: 8,
          signed_in: 1,
          errors: 2,
        },
      ],
      days: [
        { day: "2026-11-14", signed_out: 5, signed_in: 1 },
        { day: "2026-11-15", signed_out: 7, signed_in: 3 },
      ],
    },
    error: null,
    refetch: vi.fn(),
  };
});

describe("AdminUsagePage", () => {
  it("renders the window line from the server's dates", () => {
    wrap(<AdminUsagePage />);
    expect(screen.getByText(/2026/)).toBeTruthy();
  });

  it("renders a route row with its signed-out and signed-in counts", () => {
    wrap(<AdminUsagePage />);
    const row = screen.getByText("/api/agencies").closest("tr") as HTMLElement;
    const cells = Array.from(row.querySelectorAll("td, th")).map(
      (cell) => cell.textContent,
    );
    expect(cells).toEqual(["/api/agencies", "GET", "12", "3", "0"]);
  });

  it("stacks each day's signed-out and signed-in counts against the busiest day", () => {
    const { container } = wrap(<AdminUsagePage />);
    const widths = (segment: string) =>
      Array.from(
        container.querySelectorAll<HTMLElement>(`[data-segment="${segment}"]`),
      ).map((el) => el.style.width);
    // 2026-11-15 is the busiest day (10): 7 / 3 of it.
    expect(widths("signed_out")).toEqual(["50%", "70%"]);
    expect(widths("signed_in")).toEqual(["10%", "30%"]);
  });

  it("defaults the window to 30 days", () => {
    wrap(<AdminUsagePage />);
    expect(lastArgs).toMatchObject({ days: 30 });
  });

  it("reads the days selector from the URL so a chosen window is linkable", () => {
    wrapAt(<AdminUsagePage />, "/admin/usage?days=7");
    expect(lastArgs).toMatchObject({ days: 7 });
    expect(screen.getByRole("button", { name: /7/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("falls back to 30 days for a days value the selector does not offer", () => {
    wrapAt(<AdminUsagePage />, "/admin/usage?days=14");
    expect(lastArgs).toMatchObject({ days: 30 });
  });

  it("shows an error, not an empty table, when the usage query failed", () => {
    usageReturn = {
      data: undefined,
      error: new Error("boom"),
      refetch: vi.fn(),
    };
    wrap(<AdminUsagePage />);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.queryByText(i18n.t("admin.usage.by_route.empty"))).toBeNull();
  });

  it("does not claim the window is empty while it is still loading", () => {
    usageReturn = { data: undefined, error: null, refetch: vi.fn() };
    wrap(<AdminUsagePage />);
    expect(screen.queryByText(i18n.t("admin.usage.by_route.empty"))).toBeNull();
    expect(screen.queryByText(i18n.t("admin.usage.by_day.empty"))).toBeNull();
  });

  it("shows the empty state once a settled response has no routes or days", () => {
    usageReturn = {
      data: { from: "2026-11-01", to: "2026-11-15", routes: [], days: [] },
      error: null,
      refetch: vi.fn(),
    };
    wrap(<AdminUsagePage />);
    expect(screen.getByText(i18n.t("admin.usage.by_route.empty"))).toBeTruthy();
    expect(screen.getByText(i18n.t("admin.usage.by_day.empty"))).toBeTruthy();
  });
});
