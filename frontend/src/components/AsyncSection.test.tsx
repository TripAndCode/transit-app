import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../test/renderWithProviders";
import { ApiError } from "../api/client";
import { AsyncSection } from "./AsyncSection";
import { AUTO_RETRY_DELAYS_MS } from "./useAutoRetry";

type Row = { id: number };

type Overrides = Partial<React.ComponentProps<typeof AsyncSection<Row[]>>>;

function renderSection(overrides: Overrides = {}) {
  return renderWithProviders(
    <MemoryRouter>
      <AsyncSection<Row[]>
        loading={false}
        error={null}
        data={[{ id: 1 }]}
        empty={<div>nothing here</div>}
        skeleton={<div data-testid="skel" />}
        {...overrides}
      >
        {(rows) => <div>rows: {rows.length}</div>}
      </AsyncSection>
    </MemoryRouter>,
  );
}

describe("AsyncSection", () => {
  it("renders children with the data when loaded and non-empty", () => {
    renderSection();
    expect(screen.getByText("rows: 1")).toBeInTheDocument();
    expect(screen.queryByTestId("skel")).not.toBeInTheDocument();
  });

  it("renders only the skeleton while loading", () => {
    renderSection({ loading: true, data: undefined });
    expect(screen.getByTestId("skel")).toBeInTheDocument();
    expect(screen.queryByText(/^rows:/)).not.toBeInTheDocument();
    expect(screen.queryByText("nothing here")).not.toBeInTheDocument();
  });

  it("shows the skeleton over already-fetched data during a refetch", () => {
    // isFetching-fed loading must replace stale data with the skeleton, not
    // just fill an otherwise-empty slot — that's the entire reason `loading`
    // is a plain boolean instead of being derived from `data === undefined`.
    renderSection({ loading: true });
    expect(screen.getByTestId("skel")).toBeInTheDocument();
    expect(screen.queryByText(/^rows:/)).not.toBeInTheDocument();
  });

  it("renders nothing when not loading, not errored, and data is undefined", () => {
    renderSection({ data: undefined });
    expect(screen.queryByTestId("skel")).not.toBeInTheDocument();
    expect(screen.queryByText(/^rows:/)).not.toBeInTheDocument();
    expect(screen.queryByText("nothing here")).not.toBeInTheDocument();
  });

  it("renders the empty node instead of children when the data has no content", () => {
    renderSection({ data: [], hasContent: (rows) => rows.length > 0 });
    expect(screen.getByText("nothing here")).toBeInTheDocument();
    expect(screen.queryByText(/^rows:/)).not.toBeInTheDocument();
  });

  it("renders an error instead of children when the query failed", () => {
    renderSection({ error: new Error("boom"), data: undefined });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(/^rows:/)).not.toBeInTheDocument();
  });

  it("wires the retry control through to onRetry", async () => {
    // A non-transient class (server 5xx), not a plain Error/TypeError -- a
    // transient network/timeout error auto-retries first (see the "quiet
    // auto-retry" suite below) rather than showing a button immediately.
    const onRetry = vi.fn();
    renderSection({ error: new ApiError(500, "boom"), data: undefined, onRetry });
    await userEvent.click(screen.getByRole("button"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("shows the error rather than stale content when a refetch fails", () => {
    // keepPreviousData leaves `data` populated while a later fetch errors, so
    // both branches can be live at once. Rendering content underneath the
    // failure would leave a stale figure on screen with nothing marking it
    // stale.
    renderSection({ error: new Error("boom"), data: [{ id: 1 }] });
    expect(screen.queryByText(/^rows:/)).not.toBeInTheDocument();
  });

  it("keeps the failure on screen while a retry is in flight", () => {
    // A retry leaves `error` set and the query fetching at the same time.
    // Swapping to the skeleton here would make the retry control vanish from
    // under the pointer that just pressed it, then reappear. Non-transient
    // class, same reasoning as above.
    renderSection({ loading: true, error: new ApiError(500, "boom"), data: undefined, onRetry: () => {} });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByTestId("skel")).not.toBeInTheDocument();
  });

  describe("quiet auto-retry", () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    it("shows the skeleton, not the banner, on a transient network error while quietly retrying", () => {
      vi.useFakeTimers();
      const onRetry = vi.fn();
      renderSection({ error: new TypeError("Failed to fetch"), data: undefined, onRetry });
      expect(screen.getByTestId("skel")).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(onRetry).not.toHaveBeenCalled();
    });

    it("calls onRetry twice on backoff, then falls back to the visible banner", () => {
      vi.useFakeTimers();
      const onRetry = vi.fn();
      renderSection({ error: new TypeError("Failed to fetch"), data: undefined, onRetry });

      act(() => {
        vi.advanceTimersByTime(AUTO_RETRY_DELAYS_MS[0]);
      });
      expect(onRetry).toHaveBeenCalledTimes(1);
      expect(screen.getByTestId("skel")).toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(AUTO_RETRY_DELAYS_MS[1]);
      });
      expect(onRetry).toHaveBeenCalledTimes(2);
      expect(screen.getByRole("alert")).toBeInTheDocument();
      expect(screen.getByRole("button")).toBeInTheDocument();
    });

    it("shows the banner immediately for a non-transient error, with no quiet phase", () => {
      vi.useFakeTimers();
      const onRetry = vi.fn();
      renderSection({ error: new ApiError(404, ""), data: undefined, onRetry });
      expect(screen.getByRole("alert")).toBeInTheDocument();
      act(() => {
        vi.advanceTimersByTime(60_000);
      });
      expect(onRetry).not.toHaveBeenCalled();
    });
  });

  it("adds no wrapper element of its own", () => {
    // Call sites drop this straight into grid and flex containers, where an
    // extra element would become a stray grid item.
    const { container } = renderSection();
    expect(container.childElementCount).toBe(1);
    expect(container.firstElementChild?.textContent).toBe("rows: 1");
  });
});
