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
  it("renders the 4 main nav items with their label and subtitle", () => {
    renderSidebar();
    expect(screen.getByText("Overview")).toBeTruthy();
    expect(screen.getByText("What's happening right now")).toBeTruthy();
    expect(screen.getByText("Map")).toBeTruthy();
    expect(screen.getByText("Where it's happening")).toBeTruthy();
    expect(screen.getByText("Analysis")).toBeTruthy();
    expect(screen.getByText("When and why delays happen")).toBeTruthy();
    expect(screen.getByText("Agencies")).toBeTruthy();
    expect(screen.getByText("How you compare to others")).toBeTruthy();
  });

  it("renders Ask as a distinct CTA, and does not render Live (moved to the header)", () => {
    renderSidebar();
    expect(screen.getByText("Ask")).toBeTruthy();
    expect(screen.queryByText("Latest observations")).toBeNull();
  });

  it("marks the current route's nav link as active", () => {
    renderSidebar("/agencies/1/map");
    const mapLink = screen.getByRole("link", { name: /Map/ });
    expect(mapLink.getAttribute("aria-current")).toBe("page");
  });

  it("renders the brand block above the nav items", () => {
    renderSidebar();
    expect(screen.getByText("Delay Dashboard")).toBeTruthy();
    expect(screen.getByText("Real-time × Timetable")).toBeTruthy();
  });

  it("renders the brand block even when there is no agencyId, but not the nav items", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <MemoryRouter initialEntries={["/"]}>
          <Sidebar />
        </MemoryRouter>
      </I18nextProvider>
    );
    expect(screen.getByText("Delay Dashboard")).toBeTruthy();
    expect(screen.getByText("Real-time × Timetable")).toBeTruthy();
    expect(screen.queryByText("Overview")).toBeNull();
  });
});
