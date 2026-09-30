import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { SavedExportTab } from "./SavedExportTab";

vi.mock("../routes/lazyTabs", () => ({
  loadReportsHomeTab: () => Promise.resolve({ default: () => <div>reports-home</div> }),
  loadAnalysisTab: () =>
    Promise.resolve({
      default: ({ reportTypes, defaultReport }: { reportTypes: readonly string[]; defaultReport?: string }) => (
        <div>
          analysis-tab:{reportTypes.join(",")} default:{defaultReport ?? "none"}
        </div>
      ),
    }),
}));

function open(path: string) {
  const router = createMemoryRouter([{ path: "agencies/:agencyId/reports", element: <SavedExportTab /> }], {
    initialEntries: [path],
  });
  renderWithProviders(<RouterProvider router={router} />);
  return router;
}

const href = (name: string) =>
  new URLSearchParams(screen.getByRole("link", { name }).getAttribute("href")!.split("?")[1] ?? "");

describe("SavedExportTab", () => {
  it("shows the printable period summary by default", async () => {
    open("/agencies/9/reports");
    expect(await screen.findByText("reports-home")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Period summary" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Saved analyses" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Council summary & delay reference" })).not.toHaveAttribute("aria-current");
  });

  it("keeps the shared scope on every document link, but not another document's report", async () => {
    open("/agencies/9/reports?report=council_summary&from=2026-08-01&to=2026-08-31");
    await screen.findByText(/^analysis-tab:council_summary,delay_certificate/);
    expect(screen.getByRole("link", { name: "Saved analyses" }).getAttribute("href")).toMatch(/^\/agencies\/9\/reports\?/);
    expect(href("Saved analyses").get("from")).toBe("2026-08-01");
    expect(href("Saved analyses").get("doc")).toBe("saved");
    expect(href("Council summary & delay reference").get("doc")).toBe("council");
    expect(href("Period summary").get("to")).toBe("2026-08-31");
    expect(href("Period summary").has("doc")).toBe(false);
    expect(href("Period summary").has("report")).toBe(false);
  });

  it.each([
    ["?doc=council", "council_summary"],
    ["?doc=certificate", "delay_certificate"],
    ["?report=delay_certificate", "council_summary"],
  ])("opens the export documents for %s", async (search, defaultReport) => {
    open(`/agencies/9/reports${search}`);
    expect(await screen.findByText(`analysis-tab:council_summary,delay_certificate default:${defaultReport}`)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Council summary & delay reference" })).toHaveAttribute("aria-current", "page");
  });

  it("shows the saved analyses for doc=saved", async () => {
    open("/agencies/9/reports?doc=saved");
    await screen.findByText("reports-home");
    expect(screen.getByRole("link", { name: "Saved analyses" })).toHaveAttribute("aria-current", "page");
  });

  it("replaces a Saved & export view param with its document", async () => {
    const router = open("/agencies/9/reports?view=saved&from=2026-09-01");
    await screen.findByText("reports-home");
    expect(router.state.historyAction).toBe("REPLACE");
    const params = new URLSearchParams(router.state.location.search);
    expect(params.get("doc")).toBe("saved");
    expect(params.has("view")).toBe(false);
    expect(params.get("from")).toBe("2026-09-01");
  });
});
