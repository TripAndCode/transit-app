import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../../i18n";
import { AdminAgenciesPage } from "../AdminAgenciesPage";

const createMutateAsync = vi.fn().mockResolvedValue({});
const patchMutateAsync = vi.fn().mockResolvedValue({});
const createReset = vi.fn();
const patchReset = vi.fn();
const delMutate = vi.fn();
const restoreMutate = vi.fn();

let delState: { isPending: boolean; variables: number | undefined } = { isPending: false, variables: undefined };
let restoreState: { isPending: boolean; variables: number | undefined } = { isPending: false, variables: undefined };

// Mock the admin API module
vi.mock("../../../api/admin", () => ({
  useAdminAgencies: () => ({
    data: [
      {
        agency_id: 1,
        agency_name: "Aomori Bus",
        feed_url: "http://feed.example.com",
        static_url: null,
        ingest_strategy: "aomori_regex",
        trip_id_pattern: null,
        deleted_at: null,
      },
      {
        agency_id: 2,
        agency_name: "Deleted Bus",
        feed_url: "http://del.example.com",
        static_url: null,
        ingest_strategy: null,
        trip_id_pattern: null,
        deleted_at: "2026-06-01T00:00:00Z",
      },
    ],
    isLoading: false,
    error: null,
  }),
  useCreateAgencyAdmin: () => ({ mutateAsync: createMutateAsync, isPending: false, error: null, reset: createReset }),
  usePatchAgency: () => ({ mutateAsync: patchMutateAsync, isPending: false, error: null, reset: patchReset }),
  useDeleteAgency: () => ({ mutate: delMutate, ...delState }),
  useRestoreAgency: () => ({ mutate: restoreMutate, ...restoreState }),
}));

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter>{ui}</MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>
  );
}

describe("AdminAgenciesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createMutateAsync.mockResolvedValue({});
    patchMutateAsync.mockResolvedValue({});
    delState = { isPending: false, variables: undefined };
    restoreState = { isPending: false, variables: undefined };
  });

  it("renders active and deleted rows", () => {
    wrap(<AdminAgenciesPage />);
    expect(screen.getByText("Aomori Bus")).toBeTruthy();
    expect(screen.getByText("Deleted Bus")).toBeTruthy();
  });

  it("shows Add agency button", () => {
    wrap(<AdminAgenciesPage />);
    expect(screen.getByRole("button", { name: /add agency/i })).toBeTruthy();
  });

  it("opens form on Add click and resets stale mutation state", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByRole("button", { name: /add agency/i }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(createReset).toHaveBeenCalled();
    expect(patchReset).toHaveBeenCalled();
  });

  it("resets stale mutation state when closing the form", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByRole("button", { name: /add agency/i }));
    const dialog = screen.getByRole("dialog");
    createReset.mockClear();
    patchReset.mockClear();
    await user.click(within(dialog).getByRole("button", { name: /cancel/i }));
    expect(createReset).toHaveBeenCalled();
    expect(patchReset).toHaveBeenCalled();
  });

  it("submits the add form and coalesces blank optional fields to null", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByRole("button", { name: /add agency/i }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText(/agency name/i), "New Co");
    await user.type(within(dialog).getByLabelText(/feed url/i), "http://new.example.com");
    await user.click(within(dialog).getByRole("button", { name: /^add$/i }));
    expect(createMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        agency_name: "New Co",
        feed_url: "http://new.example.com",
        static_url: null,
        ingest_strategy: null,
        trip_id_pattern: null,
      })
    );
  });

  it("calls delete mutation with the agency id after confirming", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByRole("button", { name: /delete/i }));
    expect(delMutate).toHaveBeenCalledWith(1);
  });

  it("does not call delete mutation when confirm is declined", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByRole("button", { name: /delete/i }));
    expect(delMutate).not.toHaveBeenCalled();
  });

  it("calls restore mutation with the agency id", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.click(screen.getByRole("button", { name: /restore/i }));
    expect(restoreMutate).toHaveBeenCalledWith(2);
  });

  it("disables the restore button only for the row actually being restored", () => {
    restoreState = { isPending: true, variables: 2 };
    wrap(<AdminAgenciesPage />);
    expect(screen.getByRole("button", { name: /restore/i })).toHaveProperty("disabled", true);
  });

  it("does not disable delete when a different row's delete is pending", () => {
    delState = { isPending: true, variables: 999 };
    wrap(<AdminAgenciesPage />);
    expect(screen.getByRole("button", { name: /delete/i })).toHaveProperty("disabled", false);
  });

  it("filters rows by agency name via the search input", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.type(screen.getByPlaceholderText("Search by name"), "Deleted");
    expect(screen.queryByText("Aomori Bus")).toBeNull();
    expect(screen.getByText("Deleted Bus")).toBeTruthy();
  });

  it("shows an empty-state row when the search matches nothing", async () => {
    const user = userEvent.setup();
    wrap(<AdminAgenciesPage />);
    await user.type(screen.getByPlaceholderText("Search by name"), "nonexistent-agency");
    expect(screen.getByText("No agencies found.")).toBeTruthy();
  });
});
