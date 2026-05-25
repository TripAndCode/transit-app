// frontend/src/components/MoversList.tsx
import { useTranslation } from "react-i18next";

import type { OverviewMover } from "../api/types";
import { InlineSparkline } from "./InlineSparkline";

type Props = {
  direction: "worse" | "better";
  movers: OverviewMover[];
};

export function MoversList({ direction, movers }: Props) {
  const { t } = useTranslation();
  if (movers.length === 0) return null;
  const sectionKey =
    direction === "worse" ? "overview.section_movers_worse" : "overview.section_movers_better";
  const streakKey =
    direction === "worse" ? "overview.streak_worse" : "overview.streak_better";
  const perfKey =
    direction === "worse" ? "overview.mover.worsened" : "overview.mover.improved";

  // Tonal-gray sparkline for movers (color is reserved for delta chips)
  const sparkAccent = "#475569";

  const chipClass = direction === "worse" ? "ov-chip-up" : "ov-chip-down";
  const rankClass = direction === "worse" ? "ov-rank-worse" : "ov-rank-better";
  const streakDotClass = direction === "worse" ? "" : "good";
  const arrow = direction === "worse" ? "▲" : "▼";

  return (
    <div className="ov-card">
      <p className="ov-card-eyebrow">{t(sectionKey)}</p>
      <div className="ov-mover-list">
        {movers.map((m, idx) => {
          const pctText = `${Math.abs(m.delta_pct).toFixed(0)}%`;
          const minText = t(perfKey, {
            val: Math.abs(m.delta_min).toFixed(1),
          });
          return (
            <div className="ov-mover-row" key={m.route_code}>
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
              <span className={`ov-chip ov-chip-lg ${chipClass}`}>
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
