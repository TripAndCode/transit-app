import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { RequireAdmin } from "./RequireAdmin";

const mockUseSession = vi.fn();
vi.mock("../api/auth", () => ({
  useSession: () => mockUseSession(),
}));

function renderGuarded() {
  return render(
    <MemoryRouter initialEntries={["/admin/users"]}>
      <Routes>
        <Route path="/login" element={<div>login page</div>} />
        <Route path="/" element={<div>home page</div>} />
        <Route
          path="/admin/users"
          element={
            <RequireAdmin>
              <div>admin content</div>
            </RequireAdmin>
          }
        />
      </Routes>
    </MemoryRouter>
  );
}

describe("RequireAdmin", () => {
  it("renders a loading placeholder instead of blank while the session is resolving", () => {
    mockUseSession.mockReturnValue({ data: undefined, isLoading: true });
    renderGuarded();
    expect(screen.queryByText("admin content")).toBeNull();
    expect(screen.queryByText("login page")).toBeNull();
  });

  it("redirects an unauthenticated visitor to /login", () => {
    mockUseSession.mockReturnValue({ data: null, isLoading: false });
    renderGuarded();
    expect(screen.getByText("login page")).toBeTruthy();
  });

  it("redirects a signed-in non-admin to /", () => {
    mockUseSession.mockReturnValue({ data: { role: "user" }, isLoading: false });
    renderGuarded();
    expect(screen.getByText("home page")).toBeTruthy();
  });

  it("renders the guarded content for a signed-in admin", () => {
    mockUseSession.mockReturnValue({ data: { role: "admin" }, isLoading: false });
    renderGuarded();
    expect(screen.getByText("admin content")).toBeTruthy();
  });
});
