// frontend/src/components/OverviewMoversList.tsx
// Overview-specific movers list (direction/worse/better variant).
import { useTranslation } from "react-i18next";

import type { OverviewMover } from "../api/types";
import { InlineSparkline } from "./InlineSparkline";

type Props = {
  direction: "worse" | "better";
  movers: OverviewMover[];
  /** Maximum rows to render. Card variant: 3 (default). Modal: 10. */
  limit?: number;
  /** "card" (default) styles the wrapper as a clickable card; "modal"
   *  drops the .ov-card chrome so the modal body owns layout. */
  variant?: "card" | "modal";
  /** When set, the card becomes clickable and opens the modal. */
  onClick?: () => void;
  /** ISO dates of the current 7-day comparison window. When provided
   *  (modal variant), an explainer line describes the methodology. */
  windowFrom?: string;
  windowTo?: string;
};

export function MoversList({
  direction,
  movers,
  limit = 3,
  variant = "card",
  onClick,
  windowFrom,
  windowTo,
}: Props) {
  const { t } = useTranslation();
  if (movers.length === 0) return null;
  const visible = movers.slice(0, limit);
  const sectionKey =
    direction === "worse" ? "overview.section_movers_worse" : "overview.section_movers_better";
  const streakKey =
    direction === "worse" ? "overview.streak_worse" : "overview.streak_better";
  const perfKey =
    direction === "worse" ? "overview.mover.worsened" : "overview.mover.improved";

  // Tonal-gray sparkline for movers (color is reserved for delta chips)
  const sparkAccent = "var(--trend-neutral)";

  const chipClass = direction === "worse" ? "ov-chip-up" : "ov-chip-down";
  const rankClass = direction === "worse" ? "ov-rank-worse" : "ov-rank-better";
  const streakDotClass = direction === "worse" ? "" : "good";
  const arrow = direction === "worse" ? "▲" : "▼";

  const clickable = !!onClick;
  const wrapperClass =
    variant === "modal"
      ? "ov-mover-list-modal"
      : `ov-card${clickable ? " ov-clickable" : ""}`;
  const interactiveProps = clickable
    ? {
        tabIndex: 0,
        role: "button",
        onClick,
        onKeyDown: (e: React.KeyboardEvent) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onClick?.();
          }
        },
      }
    : {};

  const isModal = variant === "modal";

  return (
    <div className={wrapperClass} {...interactiveProps}>
      {!isModal && <p className="ov-card-eyebrow">{t(sectionKey)}</p>}
      {isModal && windowFrom && windowTo && (
        <p className="ov-modal-explainer">
          {t("overview.movers_explainer", { from: windowFrom, to: windowTo })}
        </p>
      )}
      <div className="ov-mover-list">
        {visible.map((m, idx) => {
          const pctText = `${Math.abs(m.delta_pct).toFixed(0)}%`;
          const minText = t(perfKey, {
            val: Math.abs(m.delta_min).toFixed(1),
          });
          return (
            <div
              className={`ov-mover-row${isModal ? " ov-mover-row-modal" : ""}`}
              key={m.route_code}
            >
              <span className={`ov-rank ${rankClass}`}>{idx + 1}</span>
              <div className="ov-mover-text">
                <div className="ov-mover-name">
                  {m.route_short_name
                    ? `${m.route_short_name} (${m.route_code})`
                    : m.route_code}
                </div>
                <div className="ov-mover-sub">{minText}</div>
                {m.streak_weeks >= 2 && (
                  <div className={`ov-mover-streak ${streakDotClass}`}>
                    {t(streakKey, { count: m.streak_weeks })}
                  </div>
                )}
              </div>
              {isModal && (
                <div className="ov-mover-cmp">
                  {t("overview.mover.week_compare", {
                    prev: m.previous_avg_min.toFixed(1),
                    cur: m.current_avg_min.toFixed(1),
                  })}
                </div>
              )}
              <span
                className={`ov-chip ov-chip-lg ${chipClass}`}
                title={t("overview.mover.delta_tooltip")}
              >
                {arrow} {pctText}
              </span>
              <div className="ov-mover-spark">
                {m.sparkline_points.length >= 2 && (
                  <InlineSparkline
                    points={m.sparkline_points}
                    width={80}
                    height={28}
                    accent={sparkAccent}
                    forceAccent
                    showLabels={false}
                    showEndDot={false}
                  />
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
