import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { RequireAuth } from "./RequireAuth";

const mockUseConfig = vi.fn();
const mockUseSession = vi.fn();
vi.mock("../api/config", () => ({ useConfig: () => mockUseConfig() }));
vi.mock("../api/auth", () => ({ useSession: () => mockUseSession() }));

type Probe = { data: unknown; isError: boolean; error: unknown; refetch: () => void };
const settled = (data: unknown): Probe => ({ data, isError: false, error: null, refetch: vi.fn() });
const pending = (): Probe => ({ data: undefined, isError: false, error: null, refetch: vi.fn() });
const failed = (): Probe => ({ data: undefined, isError: true, error: new Error("down"), refetch: vi.fn() });

function LocationProbe() {
  const l = useLocation();
  return <div>at:{l.pathname + l.search}</div>;
}

function renderAt(path: string) {
  return renderWithProviders(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/welcome" element={<LocationProbe />} />
        <Route path="/login" element={<LocationProbe />} />
        <Route path="*" element={<RequireAuth><div>app content</div></RequireAuth>} />
      </Routes>
    </MemoryRouter>,
  );
}

const signedIn = { user_id: 1, email: "a@b", role: "user" };

describe("RequireAuth", () => {
  beforeEach(() => {
    mockUseConfig.mockReturnValue(settled({ auth_enabled: true, local_admin_enabled: false, login_required: true }));
    mockUseSession.mockReturnValue(settled(null));
  });

  it("renders the app without waiting for the session when sign-in is not required", () => {
    mockUseConfig.mockReturnValue(settled({ auth_enabled: false, local_admin_enabled: false, login_required: false }));
    mockUseSession.mockReturnValue(pending());
    renderAt("/agencies/1/operations");
    expect(screen.getByText("app content")).toBeTruthy();
  });

  it("treats a config without the field, from an older backend, as not requiring sign-in", () => {
    mockUseConfig.mockReturnValue(settled({ auth_enabled: true, local_admin_enabled: false }));
    renderAt("/agencies/1/operations");
    expect(screen.getByText("app content")).toBeTruthy();
  });

  it("renders the app for a signed-in visitor", () => {
    mockUseSession.mockReturnValue(settled(signedIn));
    renderAt("/agencies/1/operations");
    expect(screen.getByText("app content")).toBeTruthy();
  });

  it("sends a signed-out visitor at the root to the landing page", () => {
    renderAt("/");
    expect(screen.getByText("at:/welcome")).toBeTruthy();
  });

  it("sends a signed-out deep link to /login with its path, query and fragment as next", () => {
    const deepLink = "/agencies/1/analysis/ranking?route=12#chart";
    renderAt(deepLink);
    expect(screen.getByText(`at:/login?next=${encodeURIComponent(deepLink)}`)).toBeTruthy();
  });

  it("shows neither the app nor a redirect while the config probe is pending", () => {
    mockUseConfig.mockReturnValue(pending());
    renderAt("/agencies/1/operations");
    expect(screen.queryByText("app content")).toBeNull();
    expect(screen.queryByText(/^at:/)).toBeNull();
  });

  it("offers a retry instead of guessing when the config probe fails", () => {
    mockUseConfig.mockReturnValue(failed());
    renderAt("/agencies/1/operations");
    expect(screen.queryByText("app content")).toBeNull();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("keeps a signed-in visitor's app when a background session refetch fails", () => {
    mockUseSession.mockReturnValue({ ...settled(signedIn), isError: true, error: new Error("blip") });
    renderAt("/agencies/1/operations");
    expect(screen.getByText("app content")).toBeTruthy();
  });
});
