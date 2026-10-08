import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useNavigate } from "react-router-dom";
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
  key: "warnkey000000001",
  acked: false,
};

const INFO_ALERT = {
  level: "info" as const,
  code: "llm_approvals_pending",
  params: { count: 2 },
  text: "2 user(s) awaiting AI access approval",
  href: "/admin/users",
  key: "infokey000000002",
  acked: false,
};

let mockBoard: { data?: AdminBoard; isPending?: boolean; error?: Error | null; dataUpdatedAt?: number };
const boardOptions: unknown[] = [];
const ackAlert = vi.fn<(key: string) => Promise<void>>();

vi.mock("../../api/admin", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/admin")>()),
  useAdminBoard: (options: unknown) => {
    boardOptions.push(options);
    return mockBoard;
  },
  useAckBoardAlert: () => ({ mutateAsync: ackAlert }),
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
      <button type="button" onClick={() => navigate("?q=1", { replace: true })}>
        filter
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
  boardOptions.length = 0;
  ackAlert.mockReset();
  ackAlert.mockResolvedValue(undefined);
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

  it("stays open when the page only rewrites its own query string", async () => {
    mockBoard = board([WARN_ALERT]);
    wrap();
    const user = await openPopover();
    await user.click(screen.getByRole("button", { name: "filter" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("stays open when an alert's link is opened in a new tab", async () => {
    mockBoard = board([WARN_ALERT]);
    wrap();
    const user = await openPopover();
    await user.keyboard("{Control>}");
    await user.click(screen.getByRole("link", { name: i18n.t("admin.board.alert_open") }));
    await user.keyboard("{/Control}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
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

  it("excludes an alert some operator acknowledged from the unread count", () => {
    mockBoard = board([{ ...WARN_ALERT, acked: true }, INFO_ALERT]);
    wrap();
    expect(screen.getByTestId("alert-count-badge")).toHaveTextContent("1");
  });

  it("mutes the badge once every alert is acknowledged", () => {
    mockBoard = board([{ ...WARN_ALERT, acked: true }]);
    wrap();
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

/** A promise the test settles by hand, for an acknowledgement still on the way. */
function deferred() {
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function ackButton(text: string) {
  const item = screen.getAllByTestId("alert-center-item").find((li) => within(li).queryByText(text))!;
  return within(item).getByRole("button", { name: /acknowledg/i });
}

const WARN_TEXT = "Toyama Bayline: aggregates 3 days behind";
const INFO_TEXT = "2 users awaiting AI access approval";

describe("AlertCenter acknowledgement", () => {
  it("acknowledges an alert for every operator through the server, by its key", async () => {
    mockBoard = board([WARN_ALERT, INFO_ALERT]);
    wrap();
    const user = await openPopover();
    await user.click(ackButton(WARN_TEXT));
    expect(ackAlert).toHaveBeenCalledWith(WARN_ALERT.key);
  });

  it("shows an acknowledged alert as acknowledged, with nothing left to press", async () => {
    mockBoard = board([{ ...WARN_ALERT, acked: true }]);
    wrap();
    await openPopover();
    expect(screen.getByRole("button", { name: i18n.t("admin.alert_center.acked") })).toBeDisabled();
  });

  it("holds each alert's button while its own acknowledgement is on the way, whatever else is acknowledged meanwhile", async () => {
    const warn = deferred();
    ackAlert.mockImplementation((key) => (key === WARN_ALERT.key ? warn.promise : Promise.resolve()));
    mockBoard = board([WARN_ALERT, INFO_ALERT]);
    wrap();
    const user = await openPopover();
    await user.click(ackButton(WARN_TEXT));
    expect(ackButton(WARN_TEXT)).toBeDisabled();
    expect(ackButton(INFO_TEXT)).toBeEnabled();
    await user.click(ackButton(INFO_TEXT));
    expect(ackButton(WARN_TEXT)).toBeDisabled();
  });

  it("says so when an acknowledgement failed, even after another alert was acknowledged meanwhile", async () => {
    const warn = deferred();
    ackAlert.mockImplementation((key) => (key === WARN_ALERT.key ? warn.promise : Promise.resolve()));
    mockBoard = board([WARN_ALERT, INFO_ALERT]);
    wrap();
    const user = await openPopover();
    await user.click(ackButton(WARN_TEXT));
    await user.click(ackButton(INFO_TEXT));
    warn.reject(new Error("503"));
    expect(await screen.findByText(i18n.t("admin.alert_center.ack_failed"))).toBeInTheDocument();
    const warnItem = screen.getAllByTestId("alert-center-item").find((li) => within(li).queryByText(WARN_TEXT))!;
    expect(within(warnItem).getByText(i18n.t("admin.alert_center.ack_failed"))).toBeInTheDocument();
    expect(ackButton(WARN_TEXT)).toBeEnabled();
    expect(screen.getByTestId("alert-count-badge")).toHaveTextContent("2");
  });
});
