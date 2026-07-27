import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { LoginPage } from "./LoginPage";
import { ApiError } from "../api/client";

const mockApiPost = vi.fn();
let mockConfig: { auth_enabled: boolean; local_admin_enabled: boolean } | undefined;

vi.mock("../api/config", () => ({
  useConfig: () => ({ data: mockConfig }),
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiPost: (...args: unknown[]) => mockApiPost(...args) };
});

function renderLogin(path = "/login") {
  return render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter initialEntries={[path]}>
        <LoginPage />
      </MemoryRouter>
    </I18nextProvider>
  );
}

describe("LoginPage", () => {
  const assignSpy = vi.fn();

  beforeEach(() => {
    mockApiPost.mockReset();
    assignSpy.mockReset();
    mockConfig = { auth_enabled: true, local_admin_enabled: false };
    // jsdom's window.location.assign isn't configurable, so vi.spyOn can't
    // touch it directly — replace the whole `location` object instead.
    Object.defineProperty(window, "location", {
      value: { ...window.location, assign: assignSpy },
      writable: true,
    });
  });

  it("renders the SSO-disabled fallback only when neither auth method is available", () => {
    mockConfig = { auth_enabled: false, local_admin_enabled: false };
    renderLogin();
    expect(screen.getByText("SSO not configured")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sign in with Google" })).toBeNull();
  });

  it("shows the Google/GitHub buttons but no local form when only OAuth is enabled", () => {
    mockConfig = { auth_enabled: true, local_admin_enabled: false };
    renderLogin();
    expect(screen.getByRole("button", { name: "Sign in with Google" })).toBeTruthy();
    expect(screen.queryByLabelText("Username")).toBeNull();
  });

  it("shows the local username/password form but no OAuth buttons when only local admin is enabled", () => {
    mockConfig = { auth_enabled: false, local_admin_enabled: true };
    renderLogin();
    expect(screen.queryByRole("button", { name: "Sign in with Google" })).toBeNull();
    expect(screen.getByLabelText("Username")).toBeTruthy();
    expect(screen.getByLabelText("Password")).toBeTruthy();
  });

  it("shows a divider between OAuth and local login when both are enabled", () => {
    mockConfig = { auth_enabled: true, local_admin_enabled: true };
    renderLogin();
    expect(screen.getByRole("button", { name: "Sign in with Google" })).toBeTruthy();
    expect(screen.getByLabelText("Username")).toBeTruthy();
    expect(screen.getByText("or")).toBeTruthy();
  });

  it("submits the local form and redirects on success", async () => {
    mockConfig = { auth_enabled: false, local_admin_enabled: true };
    mockApiPost.mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    renderLogin("/login?next=/agencies/1/map");

    await user.type(screen.getByLabelText("Username"), "root@local");
    await user.type(screen.getByLabelText("Password"), "correct-horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(mockApiPost).toHaveBeenCalledWith("/api/auth/local/login", {
      username: "root@local",
      password: "correct-horse",
    }));
    await waitFor(() => expect(assignSpy).toHaveBeenCalledWith("/agencies/1/map"));
  });

  it("falls back to / instead of navigating to a javascript: URI in next", async () => {
    mockConfig = { auth_enabled: false, local_admin_enabled: true };
    mockApiPost.mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    renderLogin("/login?next=javascript:alert(document.cookie)");

    await user.type(screen.getByLabelText("Username"), "root@local");
    await user.type(screen.getByLabelText("Password"), "correct-horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(mockApiPost).toHaveBeenCalled());
    await waitFor(() => expect(assignSpy).toHaveBeenCalledWith("/"));
  });

  it("falls back to / instead of navigating to an absolute off-site next", async () => {
    mockConfig = { auth_enabled: false, local_admin_enabled: true };
    mockApiPost.mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    renderLogin("/login?next=https://evil.example/phish");

    await user.type(screen.getByLabelText("Username"), "root@local");
    await user.type(screen.getByLabelText("Password"), "correct-horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(mockApiPost).toHaveBeenCalled());
    await waitFor(() => expect(assignSpy).toHaveBeenCalledWith("/"));
  });

  it("falls back to / instead of navigating to a protocol-relative off-site next", async () => {
    mockConfig = { auth_enabled: false, local_admin_enabled: true };
    mockApiPost.mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    renderLogin("/login?next=//evil.example/phish");

    await user.type(screen.getByLabelText("Username"), "root@local");
    await user.type(screen.getByLabelText("Password"), "correct-horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(mockApiPost).toHaveBeenCalled());
    await waitFor(() => expect(assignSpy).toHaveBeenCalledWith("/"));
  });

  it("falls back to / instead of navigating to a backslash-authority off-site next", async () => {
    // Browsers normalize a leading backslash to a forward slash for http(s)
    // origins (WHATWG URL spec), so "/\evil.example" resolves to
    // "//evil.example" — a bypass of a naive "doesn't start with //" check.
    mockConfig = { auth_enabled: false, local_admin_enabled: true };
    mockApiPost.mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    renderLogin("/login?next=%2F%5Cevil.example%2Fphish");

    await user.type(screen.getByLabelText("Username"), "root@local");
    await user.type(screen.getByLabelText("Password"), "correct-horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(mockApiPost).toHaveBeenCalled());
    await waitFor(() => expect(assignSpy).toHaveBeenCalledWith("/"));
  });

  it("falls back to / instead of navigating to a tab-obscured off-site next", async () => {
    // The URL parser strips ASCII tab/newline before resolving, so a naive
    // string check on the raw value can miss "/\t/evil.example" collapsing
    // to protocol-relative "//evil.example".
    mockConfig = { auth_enabled: false, local_admin_enabled: true };
    mockApiPost.mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    renderLogin("/login?next=%2F%09%2Fevil.example%2Fphish");

    await user.type(screen.getByLabelText("Username"), "root@local");
    await user.type(screen.getByLabelText("Password"), "correct-horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(mockApiPost).toHaveBeenCalled());
    await waitFor(() => expect(assignSpy).toHaveBeenCalledWith("/"));
  });

  it("shows an inline error and does not redirect on 401", async () => {
    mockConfig = { auth_enabled: false, local_admin_enabled: true };
    mockApiPost.mockRejectedValue(new ApiError(401, JSON.stringify({ error: "invalid_credentials" })));
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByLabelText("Username"), "root@local");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Incorrect username or password.")).toBeTruthy();
    expect(assignSpy).not.toHaveBeenCalled();
  });

  it("shows a rate-limit-specific error on 429", async () => {
    mockConfig = { auth_enabled: false, local_admin_enabled: true };
    mockApiPost.mockRejectedValue(new ApiError(429, ""));
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByLabelText("Username"), "root@local");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Too many attempts. Please try again shortly.")).toBeTruthy();
  });
});
