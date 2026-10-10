import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { FilterContextBar } from "./FilterContextBar";
import i18n from "../i18n";
import * as hooks from "../api/hooks";
import { DataEndContext } from "../api/scope";
import type { FilterCtx } from "../api/types";

function renderBar(value: FilterCtx, dataEnd: string | null = null, routes: unknown[] = []) {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: routes, isPending: false } as never);
  return renderWithProviders(
    <DataEndContext value={dataEnd}>
      <MemoryRouter initialEntries={["/agencies/1/ask"]}>
        <Routes>
          <Route path="/agencies/:agencyId/ask" element={<FilterContextBar value={value} onChange={() => {}} />} />
        </Routes>
      </MemoryRouter>
    </DataEndContext>,
  );
}

describe("FilterContextBar", () => {
  beforeEach(async () => await i18n.changeLanguage("en"));
  afterEach(async () => {
    vi.useRealTimers();
    await i18n.changeLanguage("en");
  });

  it("marks the period with a line icon, as the rest of the app does, not an emoji", () => {
    const { container } = renderBar({ dow: "all", time_band: "all", routes: [] }, "2026-09-28");
    expect(container.textContent).not.toContain("📅");
    expect(container.querySelector("svg[aria-hidden='true']")).not.toBeNull();
  });

  it("drafts a dateless filter on the default period, which stops at the data", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-02T14:59:59Z")); // 2026-10-02 23:59:59 JST
    renderBar({ dow: "all", time_band: "all", routes: [] }, "2026-09-28");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const [from, to] = Array.from(document.querySelectorAll<HTMLInputElement>("input[type='date']"));
    expect(from.value).toBe("2026-08-30");
    expect(to.value).toBe("2026-09-28");
  });

  it("renders a custom range in the language's date style", () => {
    renderBar({ from_date: "2026-06-01", to_date: "2026-07-15", dow: "all", time_band: "all", routes: [] });
    expect(screen.getByText("Jun 1 – Jul 15, 2026")).toBeInTheDocument();
  });

  it("names the days of a weekday list instead of calling it the weekend", () => {
    renderBar({ from_date: "2026-06-01", to_date: "2026-07-15", dow: "mon,wed", time_band: "all", routes: [] });
    expect(screen.getByText(/Mon, Wed/)).toBeInTheDocument();
    expect(screen.queryByText(/Weekend/)).toBeNull();
  });

  it("sets both date inputs' lang attribute to the active UI language when editing", () => {
    renderBar({ from_date: "2026-06-01", to_date: "2026-07-15", dow: "all", time_band: "all", routes: [] });
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const inputs = document.querySelectorAll("input[type='date']");
    expect(inputs.length).toBe(2);
    inputs.forEach((el) => expect(el.getAttribute("lang")).toBe("en"));
  });

  it("shows scoped routes by their display names, joined with the language's list separator", async () => {
    const routes = [
      { route_code: "R1", route_short_name: "A1", route_long_name: "Coast Line" },
      { route_code: "R2", route_short_name: "B2", route_long_name: "Hill Line" },
    ];
    const value = { dow: "all", time_band: "all", routes: ["R1", "R2"] } as FilterCtx;
    const { unmount } = renderBar(value, null, routes);
    const en = screen.getByText(/A1/);
    expect(en.textContent).not.toContain("R1");
    expect(en.textContent).toContain("A1");
    expect(en.textContent).toContain("B2");
    expect(en.textContent).toContain(", ");
    unmount();
    await i18n.changeLanguage("ja");
    renderBar(value, null, routes);
    const ja = screen.getByText(/A1/);
    expect(ja.textContent).toContain("・");
    expect(ja.textContent).not.toContain(", ");
  });
});
