import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import type { AdminBoard } from "../../api/admin";
import { AlertCenter } from "./AlertCenter";

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

let mockBoard: { data?: AdminBoard };

vi.mock("../../api/admin", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/admin")>()),
  useAdminBoard: () => mockBoard,
}));

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <AlertCenter />
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

function board(alerts: AdminBoard["alerts"]): { data: AdminBoard } {
  return {
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
});

afterEach(() => {
  vi.useRealTimers();
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
    const warnItem = items.find((li) => within(li).queryByText(WARN_ALERT.text));
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
    expect(within(dialog).getByText(WARN_ALERT.text)).toBeInTheDocument();
    expect(within(dialog).getByText(INFO_ALERT.text)).toBeInTheDocument();
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
    expect(screen.getByText(i18n.t("admin.alert_center.empty"))).toBeInTheDocument();
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

    vi.setSystemTime(new Date("2026-09-08T00:00:01Z")); // just past the 7-day window
    wrap();
    expect(screen.getByTestId("alert-count-badge")).toHaveTextContent("1");
  });

  it("does not acknowledge across browsers/tabs when localStorage is unavailable", async () => {
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
