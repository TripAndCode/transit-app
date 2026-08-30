import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { useAnonymousFilterPersistence } from "./anonymousFilterPersistence";

const useSessionMock = vi.fn();
vi.mock("./auth", () => ({
  useSession: () => useSessionMock(),
}));

// Probe shares the same router context as the hook so it reactively sees
// whatever setSearchParams call the hook makes, mirroring
// defaultRangeAnchor.test.tsx's own probe pattern.
function Probe({ agencyId }: { agencyId: number | null }) {
  useAnonymousFilterPersistence(agencyId);
  const [params] = useSearchParams();
  return <div data-testid="params">{params.toString()}</div>;
}

function renderProbe(agencyId: number | null, initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Probe agencyId={agencyId} />
    </MemoryRouter>,
  );
}

describe("useAnonymousFilterPersistence", () => {
  beforeEach(() => {
    localStorage.clear();
    useSessionMock.mockReturnValue({ data: null, isLoading: false });
  });
  afterEach(() => vi.restoreAllMocks());

  it("does nothing while logged in", () => {
    useSessionMock.mockReturnValue({ data: { user_id: 1 }, isLoading: false });
    localStorage.setItem(
      "transit.lastFilter.1",
      JSON.stringify({ dow: "weekend" }),
    );
    renderProbe(1, "/agencies/1/overview");
    expect(screen.getByTestId("params")).toHaveTextContent("");
  });

  it("does nothing while the session is still loading", () => {
    useSessionMock.mockReturnValue({ data: null, isLoading: true });
    localStorage.setItem(
      "transit.lastFilter.1",
      JSON.stringify({ dow: "weekend" }),
    );
    renderProbe(1, "/agencies/1/overview");
    expect(screen.getByTestId("params")).toHaveTextContent("");
  });

  it("restores a stored filter on a fresh visit with no explicit filter params", () => {
    localStorage.setItem(
      "transit.lastFilter.1",
      JSON.stringify({
        from: "2026-01-01",
        to: "2026-01-31",
        dow: "weekend",
        time_band: "evening",
        service: "all",
        routes: ["A1", "B2"],
      }),
    );
    renderProbe(1, "/agencies/1/overview");
    const params = new URLSearchParams(screen.getByTestId("params").textContent ?? "");
    expect(params.get("from")).toBe("2026-01-01");
    expect(params.get("to")).toBe("2026-01-31");
    expect(params.get("dow")).toBe("weekend");
    expect(params.get("time_band")).toBe("evening");
    expect(params.get("routes")).toBe("A1,B2");
  });

  it("does not restore when the URL already has an explicit filter param", () => {
    localStorage.setItem(
      "transit.lastFilter.1",
      JSON.stringify({ dow: "weekend" }),
    );
    renderProbe(1, "/agencies/1/overview?dow=weekday");
    expect(screen.getByTestId("params")).toHaveTextContent("dow=weekday");
  });

  it("persists the current filter to localStorage for later restoration", () => {
    renderProbe(1, "/agencies/1/overview?dow=weekend&time_band=night");
    const stored = JSON.parse(localStorage.getItem("transit.lastFilter.1") ?? "{}");
    expect(stored.dow).toBe("weekend");
    expect(stored.time_band).toBe("night");
  });

  it("scopes storage per agency", () => {
    localStorage.setItem(
      "transit.lastFilter.1",
      JSON.stringify({ dow: "weekend" }),
    );
    renderProbe(2, "/agencies/2/overview");
    expect(screen.getByTestId("params")).toHaveTextContent("");
  });

  it("does nothing when agencyId is null", () => {
    renderProbe(null, "/");
    expect(screen.getByTestId("params")).toHaveTextContent("");
  });
});
