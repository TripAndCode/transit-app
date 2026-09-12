import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../test/renderWithProviders";
import { AsyncSection } from "./AsyncSection";

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
    const onRetry = vi.fn();
    renderSection({ error: new Error("boom"), data: undefined, onRetry });
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
    // under the pointer that just pressed it, then reappear.
    renderSection({ loading: true, error: new Error("boom"), data: undefined, onRetry: () => {} });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByTestId("skel")).not.toBeInTheDocument();
  });

  it("adds no wrapper element of its own", () => {
    // Call sites drop this straight into grid and flex containers, where an
    // extra element would become a stray grid item.
    const { container } = renderSection();
    expect(container.childElementCount).toBe(1);
    expect(container.firstElementChild?.textContent).toBe("rows: 1");
  });
});
