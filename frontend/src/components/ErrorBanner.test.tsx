import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { ErrorBanner } from "./ErrorBanner";
import { ApiError } from "../api/client";

describe("ErrorBanner", () => {
  it("shows a retry button for a generic server error", () => {
    renderWithProviders(<ErrorBanner error={new ApiError(500, "boom")} onRetry={vi.fn()} />);
    expect(screen.getByText(/取得できませんでした|Couldn't load/i)).toBeInTheDocument();
    expect(screen.getByRole("button")).toBeInTheDocument();
  });

  it("renders a calm 'not ready' state with no retry for aggregate_not_ready", () => {
    renderWithProviders(
      <ErrorBanner
        error={new ApiError(503, JSON.stringify({ detail: "x", code: "aggregate_not_ready" }))}
        onRetry={vi.fn()}
      />,
    );
    // calm message, not the alarming "retry" copy
    expect(screen.getByText(/まだ準備されていません|isn't ready/i)).toBeInTheDocument();
    // retry is futile here — no button even though onRetry was passed
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders a calm sign-in nudge (not a generic rate-limit banner) for the anon Ask quota 429", () => {
    renderWithProviders(
      <MemoryRouter>
        <ErrorBanner
          error={new ApiError(429, JSON.stringify({ detail: "x", code: "ask_anon_quota_exceeded" }))}
          onRetry={vi.fn()}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/free AI question limit/i);
    // Calm status, not the alarming role="alert" generic-error styling.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // Invites sign-in rather than a futile immediate retry.
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });
});
