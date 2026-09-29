import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "../i18n";
import { SavedExportTab } from "./SavedExportTab";

vi.mock("../routes/lazyTabs", () => ({
  loadReportsHomeTab: () => Promise.resolve({ default: () => <div>reports-home</div> }),
  loadAnalysisTab: () =>
    Promise.resolve({
      default: ({ reportTypes }: { reportTypes: readonly string[] }) => <div>analysis-tab:{reportTypes.join(",")}</div>,
    }),
}));

void i18n.changeLanguage("en");

function open(path: string) {
  const router = createMemoryRouter([{ path: "agencies/:agencyId/saved", element: <SavedExportTab /> }], {
    initialEntries: [path],
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <I18nextProvider i18n={i18n}>
        <RouterProvider router={router} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return router;
}

describe("SavedExportTab", () => {
  it("shows the printable period summary by default", async () => {
    open("/agencies/9/saved");
    expect(await screen.findByText("reports-home")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Period summary" })).toHaveAttribute("aria-current", "page");
  });

  it("shows the council summary and delay reference in reports view", async () => {
    open("/agencies/9/saved?view=reports");
    expect(await screen.findByText("analysis-tab:council_summary,delay_certificate")).toBeInTheDocument();
  });

  it("treats a report param for an export type as the reports view", async () => {
    open("/agencies/9/saved?report=delay_certificate");
    expect(await screen.findByText("analysis-tab:council_summary,delay_certificate")).toBeInTheDocument();
  });
});
