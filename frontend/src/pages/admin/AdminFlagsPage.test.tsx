import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { AdminFlagsPage } from "./AdminFlagsPage";

const patchMutate = vi.fn();
const useFeatureFlagsMock = vi.fn();

vi.mock("../../api/admin", () => ({
  useFeatureFlags: () => useFeatureFlagsMock(),
  usePatchFeatureFlag: () => ({ mutate: patchMutate, isPending: false, error: null }),
}));

function twoFlags() {
  return {
    data: [
      {
        key: "ask_router_enabled",
        label_key: "admin.flags.labels.askRouterEnabled",
        value: true,
        source: "env",
        env_default: true,
        updated_by: null,
        updated_at: null,
        reason: null,
      },
      {
        key: "copilot_insight_enabled",
        label_key: "admin.flags.labels.copilotInsightEnabled",
        value: true,
        source: "override",
        env_default: false,
        updated_by: 7,
        updated_at: "2026-09-20T12:00:00Z",
        reason: "rollout for pilot agencies",
      },
    ],
    isLoading: false,
    error: null,
  };
}

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <AdminFlagsPage />
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>
  );
}

describe("AdminFlagsPage", () => {
  it("routes a load failure through the shared error banner", () => {
    useFeatureFlagsMock.mockReturnValue({ data: undefined, isLoading: false, error: new Error("boom"), refetch: vi.fn() });
    wrap();
    expect(screen.getByRole("alert")).toHaveTextContent(i18n.t("errors.network"));
    expect(screen.getByRole("button", { name: i18n.t("common.retry") })).toBeInTheDocument();
  });

  beforeEach(() => {
    useFeatureFlagsMock.mockReset();
    useFeatureFlagsMock.mockReturnValue(twoFlags());
    patchMutate.mockClear();
  });

  it("renders one row per registered flag", () => {
    wrap();
    const table = within(screen.getByRole("table"));
    expect(table.getAllByRole("row")).toHaveLength(3); // header + 2 flags
  });

  it("shows an env-source pill for a flag with no override", () => {
    wrap();
    const row = screen.getByText("Ask: rules router").closest("tr")!;
    expect(within(row).getByText(/env/i)).toBeTruthy();
  });

  it("shows an override-source pill plus the updated-by/reason provenance line", () => {
    wrap();
    const row = screen.getByText("Copilot: proactive insight").closest("tr")!;
    expect(within(row).getByText(/override/i)).toBeTruthy();
    expect(within(row).getByText(/7/)).toBeTruthy();
    expect(within(row).getByText(/rollout for pilot agencies/)).toBeTruthy();
  });

  it("opens an in-page reason dialog (not window.prompt) when a toggle is clicked", async () => {
    const promptSpy = vi.spyOn(window, "prompt");
    const user = userEvent.setup();
    wrap();
    const row = screen.getByText("Ask: rules router").closest("tr")!;
    await user.click(within(row).getByRole("switch"));

    expect(promptSpy).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeTruthy();
    promptSpy.mockRestore();
  });

  it("disables the confirm button until a reason is entered", async () => {
    const user = userEvent.setup();
    wrap();
    const row = screen.getByText("Ask: rules router").closest("tr")!;
    await user.click(within(row).getByRole("switch"));

    const dialog = screen.getByRole("dialog");
    const confirmButton = within(dialog).getByRole("button", { name: /confirm|save/i });
    expect(confirmButton).toBeDisabled();

    await user.type(within(dialog).getByRole("textbox"), "turning it off for a load test");
    expect(confirmButton).not.toBeDisabled();
  });

  it("submits the PATCH with the toggled value and the typed reason", async () => {
    const user = userEvent.setup();
    wrap();
    const row = screen.getByText("Ask: rules router").closest("tr")!;
    await user.click(within(row).getByRole("switch"));

    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByRole("textbox"), "turning it off for a load test");
    await user.click(within(dialog).getByRole("button", { name: /confirm|save/i }));

    expect(patchMutate).toHaveBeenCalledWith(
      { key: "ask_router_enabled", value: false, reason: "turning it off for a load test" },
      expect.anything()
    );
  });

  it("closes the dialog without mutating on cancel", async () => {
    const user = userEvent.setup();
    wrap();
    const row = screen.getByText("Ask: rules router").closest("tr")!;
    await user.click(within(row).getByRole("switch"));
    await user.click(screen.getByRole("button", { name: /cancel/i }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(patchMutate).not.toHaveBeenCalled();
  });
});
