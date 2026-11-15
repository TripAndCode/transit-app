import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { formatDateTime } from "../../utils/format";
import type { useAdminAskFunnel, useAdminAskQueries } from "../../api/admin";
import { AdminAskOpsPage } from "./AdminAskOpsPage";

type QueriesReturn = ReturnType<typeof useAdminAskQueries>;
type FunnelReturn = ReturnType<typeof useAdminAskFunnel>;

/** Only the fields the page reads; the rest of react-query's result object
 *  is irrelevant to these tests, so the mocks are partial. */
let queriesReturn: Partial<QueriesReturn>;
let funnelReturn: Partial<FunnelReturn>;
let lastQueriesArgs: Parameters<typeof useAdminAskQueries>[0] | undefined;
let lastFunnelArgs: Parameters<typeof useAdminAskFunnel>[0] | undefined;

vi.mock("../../api/admin", () => ({
  useAdminAskQueries: (params: Parameters<typeof useAdminAskQueries>[0]) => {
    lastQueriesArgs = params;
    return queriesReturn;
  },
  useAdminAskFunnel: (params: Parameters<typeof useAdminAskFunnel>[0]) => {
    lastFunnelArgs = params;
    return funnelReturn;
  },
}));

function wrapAt(ui: React.ReactElement, path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>
  );
}

function wrap(ui: React.ReactElement) {
  return wrapAt(ui, "/admin/ask");
}

const baseRow = {
  id: 1,
  agency_id: 1,
  agency_name: "Aomori Bus",
  question: "遅延が大きい路線は?",
  route: "rag" as const,
  tool: "top_n",
  status: "ok" as const,
  cache_outcome: "miss",
  numeric_guard_triggered: false,
  created_at: "2026-09-14T03:00:00Z",
};

beforeEach(() => {
  queriesReturn = {
    data: { rows: [baseRow], next_cursor: null },
    isLoading: false,
    error: null,
  };
  funnelReturn = {
    data: {
      by_route: [
        { route: "rules", count: 10, success_count: 10 },
        { route: "nn", count: 5, success_count: 4 },
        { route: "rag", count: 3, success_count: 2 },
        { route: "no_history", count: 1, success_count: 0 },
      ],
      total: 19,
      providers: null,
    },
    isLoading: false,
    error: null,
  };
});

describe("AdminAskOpsPage", () => {
  it("renders a query row with its route pill and status", () => {
    wrap(<AdminAskOpsPage />);
    expect(screen.getByText("Aomori Bus")).toBeTruthy();
    expect(screen.getByText(/遅延が大きい路線/)).toBeTruthy();
  });

  it("shows no numeric-guard badge, since no /ask answer carries a guard verdict", () => {
    queriesReturn = {
      data: { rows: [{ ...baseRow, numeric_guard_triggered: true }], next_cursor: null },
      isLoading: false,
      error: null,
    };
    wrap(<AdminAskOpsPage />);
    expect(screen.queryByText(/数値ガード|Numeric guard/)).toBeNull();
  });

  it("renders the route funnel counts", () => {
    wrap(<AdminAskOpsPage />);
    expect(screen.getByText(/19/)).toBeTruthy();
  });

  it("shows providers as not tracked rather than fabricating a breakdown", () => {
    wrap(<AdminAskOpsPage />);
    expect(screen.getByText(/recorded per query/i)).toBeTruthy();
  });

  it("states the provider gap as one muted caption, not a titled block", () => {
    wrap(<AdminAskOpsPage />);
    const caption = screen.getByText(/recorded per query/i);
    expect(caption).toHaveTextContent(i18n.t("admin.ask_ops.funnel.providers_not_tracked"));
    expect(caption.querySelector("strong")).toBeNull();
  });

  it("routes a query-log failure through the shared error banner", () => {
    queriesReturn = { data: undefined, isLoading: false, error: new Error("boom") };
    wrap(<AdminAskOpsPage />);
    expect(screen.getByRole("alert")).toHaveTextContent(i18n.t("errors.network"));
    expect(screen.queryByText(i18n.t("admin.ask_ops.empty"))).toBeNull();
  });

  it("does not claim the query log is empty while it is loading", () => {
    queriesReturn = { data: undefined, isLoading: true, error: null };
    wrap(<AdminAskOpsPage />);
    expect(screen.queryByText(i18n.t("admin.ask_ops.empty"))).toBeNull();
  });

  it("shows no zero funnel while the funnel query is loading", () => {
    funnelReturn = { data: undefined, isLoading: true, error: null };
    wrap(<AdminAskOpsPage />);
    expect(screen.queryByText(/total/i)).toBeNull();
  });

  it("shows an error, not a zero funnel, when the funnel query failed", () => {
    funnelReturn = { data: undefined, isLoading: false, error: new Error("boom") };
    wrap(<AdminAskOpsPage />);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.queryByText(/total/i)).toBeNull();
  });

  it("formats created_at rather than printing the raw ISO string", () => {
    wrap(<AdminAskOpsPage />);
    expect(screen.queryByText(baseRow.created_at)).toBeNull();
    expect(screen.getByText(formatDateTime(baseRow.created_at))).toBeTruthy();
  });

  it("shows a cache_outcome this build has no label for verbatim, not as a raw key", () => {
    queriesReturn = {
      data: { rows: [{ ...baseRow, cache_outcome: "revalidated" }], next_cursor: null },
      isLoading: false,
      error: null,
    };
    wrap(<AdminAskOpsPage />);
    expect(screen.getByText("revalidated")).toBeTruthy();
    expect(screen.queryByText(/admin\.ask_ops\.cache_outcome/)).toBeNull();
  });

  it("reads the route filter from the URL so a filtered view is linkable", () => {
    wrapAt(<AdminAskOpsPage />, "/admin/ask?route=rag");
    const select = screen.getByLabelText(i18n.t("admin.ask_ops.col.route")) as HTMLSelectElement;
    expect(select.value).toBe("rag");
  });

  it("passes the URL's date range to both the query list and the funnel", () => {
    wrapAt(<AdminAskOpsPage />, "/admin/ask?from=2026-09-01&to=2026-09-07");
    expect(lastQueriesArgs).toMatchObject({ from: "2026-09-01", to: "2026-09-07" });
    expect(lastFunnelArgs).toMatchObject({ from: "2026-09-01", to: "2026-09-07" });
  });

  it("leaves the funnel unfiltered by route so it still shows every stage", () => {
    wrapAt(<AdminAskOpsPage />, "/admin/ask?route=rag");
    expect(lastQueriesArgs).toMatchObject({ route: "rag" });
    expect("route" in (lastFunnelArgs ?? {})).toBe(false);
  });

  it("links the kill-switch summary to /admin/flags", () => {
    wrap(<AdminAskOpsPage />);
    const link = screen.getByRole("link", { name: /feature flags/i });
    expect(link.getAttribute("href")).toBe("/admin/flags");
  });
});
