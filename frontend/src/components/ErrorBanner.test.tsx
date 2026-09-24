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

  it("renders a calm explanation (not a generic error banner) for the admin-approval-required 403", () => {
    renderWithProviders(
      <MemoryRouter>
        <ErrorBanner error={new ApiError(403, JSON.stringify({ detail: "llm_not_approved" }))} onRetry={vi.fn()} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/requires admin approval/i);
    // Calm status, not the alarming role="alert" generic-error styling.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // Not a quota/sign-in condition, so no login link and no retry button.
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("gives a distinct 'timed out' message (not the generic network copy) for a timeout error", () => {
    const err = new Error("timed out");
    err.name = "TimeoutError";
    renderWithProviders(<ErrorBanner error={err} onRetry={vi.fn()} />);
    expect(screen.getByText(/timed out/i)).toBeInTheDocument();
    expect(screen.getByRole("button")).toBeInTheDocument();
  });

  it("gives the network message for a plain network failure, distinct from the timeout copy", () => {
    renderWithProviders(<ErrorBanner error={new TypeError("Failed to fetch")} onRetry={vi.fn()} />);
    expect(screen.getByText(/connection|network/i)).toBeInTheDocument();
  });
});
