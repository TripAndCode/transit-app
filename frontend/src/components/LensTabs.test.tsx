import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { LensTabs } from "./LensTabs";

describe("LensTabs", () => {
  it("links every visible lens with the current scope and marks the active one", () => {
    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/9/analysis/when?from=2026-08-20&to=2026-09-18&routes=50&report=trend"]}>
        <LensTabs agencyId={9} active="when" />
      </MemoryRouter>,
    );
    const where = screen.getByRole("link", { name: "Where" });
    expect(where).toHaveAttribute("href", "/agencies/9/analysis/where?from=2026-08-20&to=2026-09-18&routes=50");
    expect(screen.getByRole("link", { name: "When" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("link", { name: "Prediction accuracy" })).toBeNull();
  });
});
