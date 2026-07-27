import { describe, it, expect, vi, afterEach } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { FilterContextBar } from "./FilterContextBar";
import i18n from "../i18n";
import * as hooks from "../api/hooks";
import type { FilterCtx } from "../api/types";

function renderBar(value: FilterCtx) {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isPending: false } as never);
  return renderWithProviders(
    <MemoryRouter initialEntries={["/agencies/1/ask"]}>
      <Routes>
        <Route path="/agencies/:agencyId/ask" element={<FilterContextBar value={value} onChange={() => {}} />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("FilterContextBar", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("renders the custom-range summary with the locale-aware separator, not a hardcoded ja wave dash", () => {
    renderBar({ from_date: "2026-06-01", to_date: "2026-07-15", dow: "all", time_band: "all", routes: [] });
    expect(screen.getByText("2026-06-01 – 2026-07-15")).toBeInTheDocument();
  });

  it("sets both date inputs' lang attribute to the active UI language when editing", () => {
    renderBar({ from_date: "2026-06-01", to_date: "2026-07-15", dow: "all", time_band: "all", routes: [] });
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const inputs = document.querySelectorAll("input[type='date']");
    expect(inputs.length).toBe(2);
    inputs.forEach((el) => expect(el.getAttribute("lang")).toBe("en"));
  });
});
