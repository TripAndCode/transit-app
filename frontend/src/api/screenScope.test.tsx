import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { rememberScreenScope, screenOf, useScreenQuery } from "./screenScope";

function QueryProbe({ agencyId, screens }: { agencyId: string; screens: string[] }) {
  const screenQuery = useScreenQuery();
  return (
    <ul>
      {screens.map((s) => (
        <li key={s} data-testid={s}>
          {screenQuery(agencyId, s)}
        </li>
      ))}
    </ul>
  );
}

function GoTo({ to }: { to: string }) {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(to)}>
      go
    </button>
  );
}

beforeEach(() => sessionStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("screenOf", () => {
  it("names a screen's own page and nothing under it", () => {
    expect(screenOf("/agencies/1/routes")).toEqual({ agencyId: "1", screen: "routes" });
    expect(screenOf("/agencies/1/time/")).toEqual({ agencyId: "1", screen: "time" });
    expect(screenOf("/agencies/1/routes/42")).toBeNull();
    expect(screenOf("/agencies/1")).toBeNull();
    expect(screenOf("/welcome")).toBeNull();
  });
});

describe("screen scopes", () => {
  it("serves each screen the scope it last showed, per agency", () => {
    rememberScreenScope("1", "routes", "routes=W54");
    rememberScreenScope("1", "time", "dow=sat,sun");
    rememberScreenScope("2", "routes", "from=2026-09-01");
    render(
      <MemoryRouter initialEntries={["/agencies/1/live"]}>
        <QueryProbe agencyId="1" screens={["routes", "time", "why"]} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("routes").textContent).toBe("routes=W54");
    expect(screen.getByTestId("time").textContent).toBe("dow=sat,sun");
    expect(screen.getByTestId("why").textContent).toBe("");
  });

  it("serves the screen on show its live scope, ahead of what it last recorded", () => {
    rememberScreenScope("1", "live", "from=2026-01-01&to=2026-01-07");
    render(
      <MemoryRouter initialEntries={["/agencies/1/live?from=2026-06-01&to=2026-06-07"]}>
        <QueryProbe agencyId="1" screens={["live"]} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("live").textContent).toBe("from=2026-06-01&to=2026-06-07");
  });

  it("records what each screen shows as the visitor moves between screens", async () => {
    render(
      <MemoryRouter initialEntries={["/agencies/1/routes?routes=W54"]}>
        <Routes>
          <Route
            path="*"
            element={
              <>
                <GoTo to="/agencies/1/time" />
                <QueryProbe agencyId="1" screens={["routes", "time"]} />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    screen.getByRole("button", { name: "go" }).click();
    expect(await screen.findByTestId("routes")).toHaveTextContent("routes=W54");
    expect(screen.getByTestId("time").textContent).toBe("");
  });

  it("does not record a route dossier as the Routes screen", () => {
    render(
      <MemoryRouter initialEntries={["/agencies/1/routes/42?routes=42&from=2026-06-01"]}>
        <QueryProbe agencyId="1" screens={["routes"]} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("routes").textContent).toBe("");
  });

  it("keeps working in memory when the browser refuses session storage", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota", "QuotaExceededError");
    });
    rememberScreenScope("1", "routes", "routes=K31");
    render(
      <MemoryRouter initialEntries={["/agencies/1/live"]}>
        <QueryProbe agencyId="1" screens={["routes"]} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("routes").textContent).toBe("routes=K31");
  });
});
