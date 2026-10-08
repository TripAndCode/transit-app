import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import type { AdminBoard } from "../../api/admin";
import { AlertCenter } from "./AlertCenter";
import { hashAlertKey } from "./ackedAlerts";

const WARN_ALERT = {
  level: "warn" as const,
  code: "agency_stale",
  params: { agency: "Toyama Bayline", days: 3 },
  text: "Toyama Bayline: aggregates 3 day(s) behind",
  href: "/admin/ops",
};

const INFO_ALERT = {
  level: "info" as const,
  code: "llm_approvals_pending",
  params: { count: 2 },
  text: "2 user(s) awaiting AI access approval",
  href: "/admin/users",
};

let mockBoard: { data?: AdminBoard; isPending?: boolean; error?: Error | null; dataUpdatedAt?: number };
const boardOptions: unknown[] = [];

vi.mock("../../api/admin", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/admin")>()),
  useAdminBoard: (options: unknown) => {
    boardOptions.push(options);
    return mockBoard;
  },
}));

function GoTo({ to }: { to: string }) {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate(to)}>
        go
      </button>
      <button type="button" onClick={() => navigate(-1)}>
        back
      </button>
    </>
  );
}

function wrap(path = "/admin/users") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[path]}>
          <AlertCenter />
          <GoTo to="/admin/audit" />
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

function board(alerts: AdminBoard["alerts"]): { data: AdminBoard; dataUpdatedAt: number } {
  return {
    dataUpdatedAt: Date.now(),
    data: {
      collectors: [],
      freshness: [],
      migrations: null,
      runs: [],
      alerts,
    },
  };
}

async function openPopover() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /alert/i }));
  return user;
}

beforeEach(() => {
  localStorage.clear();
  boardOptions.length = 0;
});

afterEach(() => {
  vi.useRealTimers();
});

describe("AlertCenter polling", () => {
  it("adds no poll of its own on the board page, which already polls, and polls slowly elsewhere", () => {
    mockBoard = board([]);
    const onBoard = wrap("/admin");
    expect(boardOptions.at(-1)).toEqual({ refetchInterval: false });
    onBoard.unmount();
    wrap("/admin/users");
    expect(boardOptions.at(-1)).toEqual({ refetchInterval: 60_000 });
  });
});

describe("AlertCenter states and closing", () => {
  it("does not claim all-clear while the board is loading or failed", async () => {
    mockBoard = { data: undefined, isPending: true, error: null };
    const loading = wrap();
    await openPopover();
    expect(screen.getByText(i18n.t("common.loading"))).toBeInTheDocument();
    expect(screen.queryByText(i18n.t("admin.board.alerts_none"))).toBeNull();
    loading.unmount();
    mockBoard = { data: undefined, isPending: false, error: new Error("boom") };
    wrap();
    await openPopover();
    expect(screen.getByText(i18n.t("admin.alert_center.load_failed"))).toBeInTheDocument();
  });

  it("closes when the admin navigates elsewhere", async () => {
    mockBoard = board([WARN_ALERT]);
    wrap();
    const user = await openPopover();
    await user.click(screen.getByRole("button", { name: "go" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("stays closed when history returns to the page it was opened on", async () => {
    mockBoard = board([WARN_ALERT]);
    wrap();
    const user = await openPopover();
    await user.click(screen.getByRole("button", { name: "go" }));
    await user.click(screen.getByRole("button", { name: "back" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("says when a refresh failed while it still shows the last alerts read", async () => {
    mockBoard = { ...board([WARN_ALERT]), error: new Error("boom") };
    wrap();
    await openPopover();
    expect(screen.getByText(i18n.t("admin.alert_center.refresh_failed"))).toBeInTheDocument();
    expect(screen.getAllByTestId("alert-center-item")).toHaveLength(1);
  });

  it("closes on a click outside the panel", async () => {
    mockBoard = board([WARN_ALERT]);
    wrap();
    const user = await openPopover();
    await user.click(document.querySelector(".ui-overlay-scrim")!);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("tones the badge as a warning only while a warning is unread", () => {
    mockBoard = board([INFO_ALERT]);
    const infoOnly = wrap();
    expect(screen.getByTestId("alert-count-badge")).not.toHaveClass("alert-center-badge--warn");
    infoOnly.unmount();
    mockBoard = board([WARN_ALERT]);
    wrap();
    expect(screen.getByTestId("alert-count-badge")).toHaveClass("alert-center-badge--warn");
  });

  it("follows an acknowledgement made in another tab", () => {
    mockBoard = board([WARN_ALERT]);
    wrap();
    expect(screen.getByTestId("alert-count-badge")).toHaveTextContent("1");
    const hash = hashAlertKey(WARN_ALERT.level, WARN_ALERT.text, WARN_ALERT.href);
    act(() => {
      localStorage.setItem("transit.admin.ackedAlerts", JSON.stringify({ [hash]: Date.now() + 60_000 }));
      window.dispatchEvent(new StorageEvent("storage", { key: "transit.admin.ackedAlerts" }));
    });
    expect(screen.getByTestId("alert-count-badge")).toHaveTextContent("0");
  });
});

describe("AlertCenter bell", () => {
  it("shows no badge when the board has no alerts", () => {
    mockBoard = board([]);
    wrap();
    expect(screen.queryByTestId("alert-count-badge")).not.toBeInTheDocument();
  });

  it("counts every alert as unread before anything is acknowledged", () => {
    mockBoard = board([WARN_ALERT, INFO_ALERT]);
    wrap();
    expect(screen.getByTestId("alert-count-badge")).toHaveTextContent("2");
  });

  it("excludes an already-acknowledged alert from the unread count", async () => {
    mockBoard = board([WARN_ALERT, INFO_ALERT]);
    wrap();
    const user = await openPopover();
    const items = screen.getAllByTestId("alert-center-item");
    const warnItem = items.find((li) => within(li).queryByText("Toyama Bayline: aggregates 3 days behind"));
    await user.click(within(warnItem!).getByRole("button", { name: i18n.t("admin.alert_center.ack") }));

    expect(screen.getByTestId("alert-count-badge")).toHaveTextContent("1");
  });

  it("mutes the badge once every alert is acknowledged", async () => {
    mockBoard = board([WARN_ALERT]);
    wrap();
    const user = await openPopover();
    await user.click(screen.getByRole("button", { name: i18n.t("admin.alert_center.ack") }));

    const badge = screen.getByTestId("alert-count-badge");
    expect(badge).toHaveTextContent("0");
    expect(badge.className).toContain("alert-center-badge--muted");
  });
});

describe("AlertCenter popover", () => {
  it("opens as a labelled dialog listing alerts grouped by level", async () => {
    mockBoard = board([WARN_ALERT, INFO_ALERT]);
    wrap();
    await openPopover();

    const dialog = screen.getByRole("dialog", { name: i18n.t("admin.alert_center.title") });
    expect(within(dialog).getByText(i18n.t("admin.alert_center.group.warn"))).toBeInTheDocument();
    expect(within(dialog).getByText(i18n.t("admin.alert_center.group.info"))).toBeInTheDocument();
    // Localised as the board does: a stale agency's lag is its plural count.
    expect(within(dialog).getByText("Toyama Bayline: aggregates 3 days behind")).toBeInTheDocument();
    expect(within(dialog).getByText("2 users awaiting AI access approval")).toBeInTheDocument();
    // The group heading names the level once; the items do not repeat it.
    expect(within(dialog).getAllByText(i18n.t("admin.alert_center.group.info"))).toHaveLength(1);
  });

  it("renders each alert's deep link", async () => {
    mockBoard = board([WARN_ALERT]);
    wrap();
    await openPopover();

    const link = screen.getByRole("link", { name: i18n.t("admin.board.alert_open") });
    expect(link).toHaveAttribute("href", WARN_ALERT.href);
  });

  it("omits the deep link when an alert has none", async () => {
    mockBoard = board([{ ...WARN_ALERT, href: null }]);
    wrap();
    await openPopover();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("shows the empty state when there are no alerts", async () => {
    mockBoard = board([]);
    wrap();
    await openPopover();
    expect(screen.getByText(i18n.t("admin.board.alerts_none"))).toBeInTheDocument();
  });

  it("closes on Escape and restores focus to the bell", async () => {
    mockBoard = board([WARN_ALERT]);
    wrap();
    const user = await openPopover();
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /alert/i })).toHaveFocus();
  });

  it("closes via its own close button", async () => {
    mockBoard = board([WARN_ALERT]);
    wrap();
    const user = await openPopover();
    await user.click(screen.getByRole("button", { name: i18n.t("common.close") }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("AlertCenter acknowledgement persistence", () => {
  it("keeps an acknowledgement across remounts within the 7-day window", async () => {
    mockBoard = board([WARN_ALERT]);
    const first = wrap();
    const user = await openPopover();
    await user.click(screen.getByRole("button", { name: i18n.t("admin.alert_center.ack") }));
    first.unmount();

    wrap();
    expect(screen.getByTestId("alert-count-badge")).toHaveTextContent("0");
  });

  it("expires an acknowledgement after 7 days, making the alert unread again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-09-01T00:00:00Z"));
    mockBoard = board([WARN_ALERT]);
    const first = wrap();
    const user = await openPopover();
    await user.click(screen.getByRole("button", { name: i18n.t("admin.alert_center.ack") }));
    first.unmount();

    // Well past the 7-day window: the exact edge is pinned in ackedAlerts.test.ts.
    vi.setSystemTime(new Date("2026-09-08T01:00:00Z"));
    mockBoard = board([WARN_ALERT]);
    wrap();
    expect(screen.getByTestId("alert-count-badge")).toHaveTextContent("1");
  });

  it("leaves the alert unread when the browser cannot store the acknowledgement", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked");
    });
    mockBoard = board([WARN_ALERT]);
    wrap();
    const user = await openPopover();
    await user.click(screen.getByRole("button", { name: i18n.t("admin.alert_center.ack") }));

    expect(screen.getByTestId("alert-count-badge")).toHaveTextContent("1");
    vi.restoreAllMocks();
  });
});
