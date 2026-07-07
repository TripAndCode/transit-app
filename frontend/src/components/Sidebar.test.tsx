import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { Sidebar } from "./Sidebar";

function renderSidebar(path = "/agencies/1/map") {
  return render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/agencies/:agencyId/*" element={<Sidebar />} />
        </Routes>
      </MemoryRouter>
    </I18nextProvider>
  );
}

describe("Sidebar", () => {
  it("renders all 6 nav items with their label and subtitle", () => {
    renderSidebar();
    expect(screen.getByText("Overview")).toBeTruthy();
    expect(screen.getByText("What's happening right now")).toBeTruthy();
    expect(screen.getByText("Map")).toBeTruthy();
    expect(screen.getByText("Where it's happening")).toBeTruthy();
    expect(screen.getByText("Ask")).toBeTruthy();
    expect(screen.getByText("Ask in plain language")).toBeTruthy();
    expect(screen.getByText("Latest observations")).toBeTruthy();
    expect(screen.getByText("Spot anomalies fast")).toBeTruthy();
    expect(screen.getByText("Reports")).toBeTruthy();
    expect(screen.getByText("Analyze trends")).toBeTruthy();
    expect(screen.getByText("Forecast")).toBeTruthy();
    expect(screen.getByText("Predict future delays")).toBeTruthy();
  });

  it("marks the current route's nav link as active", () => {
    renderSidebar("/agencies/1/map");
    const mapLink = screen.getByRole("link", { name: /Map/ });
    expect(mapLink.getAttribute("aria-current")).toBe("page");
  });

  it("renders an empty placeholder aside when there is no agencyId", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <MemoryRouter initialEntries={["/"]}>
          <Sidebar />
        </MemoryRouter>
      </I18nextProvider>
    );
    expect(screen.queryByText("Overview")).toBeNull();
  });
});
