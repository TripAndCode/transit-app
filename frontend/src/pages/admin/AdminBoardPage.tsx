import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { formatDateTime } from "../../utils/format";
import {
  useAdminBoard,
  useTriggerRun,
  type BoardAlert,
  type BoardCollector,
  type BoardFreshnessDay,
} from "../../api/admin";
import { EmptyState } from "../../components/EmptyState";
import { ErrorBanner } from "../../components/ErrorBanner";
import { SkeletonChart, SkeletonKpiRow, SkeletonTable } from "../../components/Skeleton";
import { Tooltip } from "../../components/Tooltip";
import { Modal } from "../../components/Modal";
import { RunTimeline } from "./RunTimeline";

type TFunction = ReturnType<typeof useTranslation>["t"];

/** One hue for the whole heatmap: the aggregated days differ only in
 *  saturation, stale days pick up the shared warning amber, and a missing day
 *  is drawn hollow rather than in a third colour. Colour alone never carries
 *  the state — every cell also has a tooltip naming it. */
const CELL_STYLES: Record<BoardFreshnessDay["state"], React.CSSProperties> = {
  fresh: { background: "var(--accent)", opacity: 0.75, border: "1px solid transparent" },
  stale: { background: "var(--color-warning, #C99A2E)", opacity: 0.55, border: "1px solid transparent" },
  missing: { background: "transparent", border: "1px dashed var(--border-subtle)" },
};

const STATUS_COLORS: Record<BoardCollector["status"], string> = {
  ok: "var(--accent)",
  warn: "var(--color-warning, #C99A2E)",
  down: "var(--color-danger, #c0392b)",
  unknown: "var(--text-tertiary)",
};

/** Midnight of the current JST day, as an absolute instant.
 *
 *  The timeline's axis is a JST civil day because the pipeline buckets on one
 *  and the server returns one; deriving it from the viewer's own timezone
 *  would slide every bar for an operator abroad. */
function jstDayStart(now: Date): Date {
  const jstNow = new Date(now.getTime() + JST_OFFSET_MS);
  return new Date(Date.UTC(jstNow.getUTCFullYear(), jstNow.getUTCMonth(), jstNow.getUTCDate()) - JST_OFFSET_MS);
}

const JST_OFFSET_MS = 9 * 3_600_000;

function collectorLabel(t: TFunction, collector: BoardCollector): string {
  return t(`admin.board.collector.${collector.key}`, { defaultValue: collector.label });
}

function alertText(t: TFunction, alert: BoardAlert): string {
  return t(`admin.board.alert.${alert.code}`, { ...alert.params, defaultValue: alert.text });
}

function Sparkline({ history }: { history: number[] }) {
  const { t } = useTranslation();
  const width = history.length * 4;
  return (
    <svg
      data-testid="collector-sparkline"
      role="img"
      aria-label={t("admin.board.sparkline_label", { ok: history.filter(Boolean).length })}
      viewBox={`0 0 ${width} 12`}
      preserveAspectRatio="none"
      style={{ width: "100%", height: 12, display: "block", marginTop: 6 }}
    >
      {history.map((cell, index) => (
        <rect
          // The sparkline is a fixed-length positional series, never reordered.
          key={index}
          x={index * 4}
          y={0}
          width={3}
          height={12}
          rx={1}
          fill={cell ? "var(--accent)" : "var(--surface-2)"}
        />
      ))}
    </svg>
  );
}

function CollectorTile({ collector }: { collector: BoardCollector }) {
  const { t } = useTranslation();
  return (
    <div
      data-testid="collector-tile"
      style={{
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-lg)",
        background: "var(--surface-1)",
        padding: "12px 14px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{collectorLabel(t, collector)}</span>
        <span
          aria-hidden="true"
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            flexShrink: 0,
            background: STATUS_COLORS[collector.status],
          }}
        />
      </div>
      <p style={{ fontSize: 17, fontWeight: 700, margin: "4px 0 0" }}>{t(`admin.board.status.${collector.status}`)}</p>
      <p style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)", margin: "2px 0 0" }}>
        {collector.last_success_at
          ? t("admin.board.last_success", { when: formatDateTime(collector.last_success_at) })
          : t("admin.board.never")}
      </p>
      {collector.detail && (
        <p style={{ fontSize: "var(--text-xs)", color: "var(--text-tertiary)", margin: "2px 0 0" }}>{collector.detail}</p>
      )}
      <Sparkline history={collector.history} />
    </div>
  );
}

export function AdminBoardPage() {
  const { t } = useTranslation();
  const { data, error, isPending, refetch } = useAdminBoard();
  const trigger = useTriggerRun();
  const [confirming, setConfirming] = useState(false);
  const confirmRef = useRef<HTMLButtonElement>(null);

  // Read once per render rather than held in state: the board re-renders on
  // every poll, so the marker and any open run's bar advance on their own
  // without a timer of this page's making.
  const now = new Date();
  const dayStart = jstDayStart(now);

  const collectors = data?.collectors ?? [];
  const freshness = data?.freshness ?? [];
  const alerts = data?.alerts ?? [];
  const runs = data?.runs ?? [];
  const dayCount = freshness[0]?.days.length ?? 0;

  // Why the control is unavailable, or null when it is usable. Kept as the
  // single source for both the visual state and the spoken reason so the two
  // can never disagree.
  const reanalyzeBlockedReason = trigger.isPending
    ? t("admin.board.reanalyze_busy_reason")
    : data == null
      ? t("admin.board.reanalyze_unavailable_reason")
      : null;
  const reanalyzeBlocked = reanalyzeBlockedReason !== null;

  // `aria-disabled` rather than `disabled`: a natively disabled button fires
  // no pointer or focus events, so the tooltip explaining why it cannot be
  // used would be unreachable by exactly the people who need it. The click
  // handler enforces the block instead.
  const reanalyzeButton = (
    <button
      type="button"
      aria-disabled={reanalyzeBlocked}
      onClick={() => {
        if (!reanalyzeBlocked) setConfirming(true);
      }}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontSize: "var(--text-sm)",
        fontFamily: "inherit",
        padding: "6px 13px",
        borderRadius: 6,
        border: "1px solid var(--border-subtle)",
        background: "transparent",
        color: "var(--text-primary)",
        cursor: reanalyzeBlocked ? "default" : "pointer",
        opacity: reanalyzeBlocked ? 0.5 : 1,
      }}
    >
      <RefreshCw size={14} strokeWidth={1.8} aria-hidden="true" />
      {t("admin.board.reanalyze")}
    </button>
  );

  return (
    <div
      data-testid="admin-board"
      aria-busy={isPending}
      style={{ padding: 24, display: "grid", gap: 16, alignContent: "start" }}
    >
      <header style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <h1 style={{ fontSize: 22, margin: 0 }}>{t("admin.board.title")}</h1>
        <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{t("admin.board.poll_note")}</span>
        <span style={{ flex: 1 }} />
        {reanalyzeBlockedReason === null ? (
          reanalyzeButton
        ) : (
          <Tooltip label={reanalyzeBlockedReason}>{reanalyzeButton}</Tooltip>
        )}
      </header>

      <Modal
        open={confirming}
        onClose={() => setConfirming(false)}
        labelledBy="board-reanalyze-confirm-title"
        initialFocusRef={confirmRef}
        style={{
          display: "grid",
          gap: 8,
          width: "min(420px, calc(100vw - 32px))",
          padding: "14px 16px",
          borderRadius: "var(--radius-lg)",
          border: "1px solid var(--border-subtle)",
          boxShadow: "var(--el-3)",
        }}
      >
        <h2 id="board-reanalyze-confirm-title" style={{ fontSize: "var(--text-sm)", fontWeight: 700, margin: 0 }}>
          {t("admin.board.reanalyze_confirm_title")}
        </h2>
        <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--text-secondary)" }}>{t("admin.board.reanalyze_confirm_body")}</p>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            ref={confirmRef}
            onClick={() => {
              setConfirming(false);
              trigger.mutate({ kind: "ingest" });
            }}
            style={{
              fontSize: "var(--text-xs)",
              fontFamily: "inherit",
              padding: "5px 12px",
              borderRadius: 6,
              border: "1px solid var(--accent)",
              background: "var(--accent)",
              color: "var(--on-accent, #fff)",
              cursor: "pointer",
            }}
          >
            {t("admin.board.reanalyze_confirm")}
          </button>
          <button
            type="button"
            onClick={() => setConfirming(false)}
            style={{
              fontSize: "var(--text-xs)",
              fontFamily: "inherit",
              padding: "5px 12px",
              borderRadius: 6,
              border: "1px solid var(--border-subtle)",
              background: "transparent",
              color: "var(--text-primary)",
              cursor: "pointer",
            }}
          >
            {t("admin.board.reanalyze_cancel")}
          </button>
        </div>
      </Modal>

      {trigger.error != null && (
        <p role="alert" style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--color-warning, #C99A2E)" }}>
          {t("admin.board.reanalyze_error")}
        </p>
      )}

      {trigger.isSuccess && (
        <p role="status" style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--text-secondary)" }}>
          {t("admin.board.reanalyze_started")}
        </p>
      )}

      {error != null && <ErrorBanner error={error} onRetry={refetch} />}

      <section aria-label={t("admin.board.collectors_title")}>
        {isPending ? (
          <SkeletonKpiRow tiles={3} />
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 10 }}>
            {collectors.map((collector) => (
              <CollectorTile key={collector.key} collector={collector} />
            ))}
          </div>
        )}
      </section>

      <section
        aria-label={t("admin.board.freshness_title")}
        style={{
          border: "1px solid var(--border-subtle)",
          borderRadius: "var(--radius-lg)",
          background: "var(--surface-1)",
          padding: "12px 14px",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            gap: 12,
            flexWrap: "wrap",
            marginBottom: 10,
            fontSize: "var(--text-xs)",
          }}
        >
          <h2 style={{ fontSize: "var(--text-sm)", fontWeight: 700, margin: 0 }}>{t("admin.board.freshness_title")}</h2>
          <p style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--text-tertiary)", display: "flex", gap: 12 }}>
            <span>■ {t("admin.board.legend_fresh")}</span>
            <span>■ {t("admin.board.legend_stale")}</span>
            <span>□ {t("admin.board.legend_missing")}</span>
          </p>
        </div>
        {isPending ? (
          <SkeletonTable rows={3} rowHeight={16} />
        ) : freshness.length === 0 ? (
          <EmptyState title={t("admin.board.freshness_empty")} />
        ) : (
          <div style={{ overflowX: "auto" }}>
            <div style={{ display: "grid", gap: 4, minWidth: 420 }}>
              {freshness.map((row) => (
                <div
                  key={row.agency_id}
                  data-testid="freshness-row"
                  style={{
                    display: "grid",
                    gridTemplateColumns: `minmax(90px, 140px) repeat(${dayCount}, minmax(0, 1fr))`,
                    gap: 3,
                    alignItems: "center",
                  }}
                >
                  <span
                    style={{
                      fontSize: "var(--text-xs)",
                      color: "var(--text-secondary)",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {row.agency_name}
                  </span>
                  {row.days.map((day) => (
                    <span
                      key={day.date}
                      data-testid="freshness-cell"
                      data-state={day.state}
                      title={
                        day.clamp_pct == null
                          ? t("admin.board.cell_tooltip", {
                              agency: row.agency_name,
                              date: day.date,
                              state: t(`admin.board.state.${day.state}`),
                            })
                          : t("admin.board.cell_tooltip_clamp", {
                              agency: row.agency_name,
                              date: day.date,
                              state: t(`admin.board.state.${day.state}`),
                              pct: day.clamp_pct,
                            })
                      }
                      style={{ display: "block", height: 16, borderRadius: 3, ...CELL_STYLES[day.state] }}
                    />
                  ))}
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      <section
        aria-label={t("admin.board.runs_title")}
        style={{
          border: "1px solid var(--border-subtle)",
          borderRadius: "var(--radius-lg)",
          background: "var(--surface-1)",
          padding: "12px 14px",
        }}
      >
        <div
          style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", marginBottom: 8 }}
        >
          <h2 style={{ fontSize: "var(--text-sm)", fontWeight: 700, margin: 0 }}>{t("admin.board.runs_title")}</h2>
          <p style={{ margin: 0, fontSize: 12, color: "var(--text-tertiary)" }}>{t("admin.board.runs_legend")}</p>
        </div>
        {isPending ? (
          <SkeletonChart height={120} />
        ) : (
          <RunTimeline runs={runs} dayStart={dayStart} now={now} />
        )}
      </section>

      <section
        aria-label={t("admin.board.alerts_title")}
        style={{
          border: "1px solid var(--border-subtle)",
          borderRadius: "var(--radius-lg)",
          background: "var(--surface-1)",
          padding: "10px 14px",
        }}
      >
        <h2 style={{ fontSize: "var(--text-sm)", fontWeight: 700, margin: "0 0 6px" }}>{t("admin.board.alerts_title")}</h2>
        {isPending ? (
          <SkeletonTable rows={2} rowHeight={20} />
        ) : alerts.length === 0 ? (
          <EmptyState title={t("admin.board.alerts_none")} />
        ) : (
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {alerts.map((alert) => (
              <li
                key={`${alert.code}:${alert.text}`}
                data-testid="board-alert"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "7px 0",
                  borderTop: "1px solid var(--surface-2)",
                  fontSize: "var(--text-xs)",
                }}
              >
                <span
                  aria-hidden="true"
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    flexShrink: 0,
                    background: alert.level === "warn" ? "var(--color-warning, #C99A2E)" : "var(--accent)",
                  }}
                />
                <span style={{ flex: 1 }}>{alertText(t, alert)}</span>
                {alert.href && (
                  <Link to={alert.href} style={{ color: "var(--accent)", fontSize: 12, textDecoration: "none" }}>
                    {t("admin.board.alert_open")}
                  </Link>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
