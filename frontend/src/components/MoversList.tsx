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

  // Tonal-gray sparkline for movers (color is reserved for delta chips)
  const sparkAccent = "#475569";

  const chipClass = direction === "worse" ? "ov-chip-up" : "ov-chip-down";
  const arrow = direction === "worse" ? "▲" : "▼";

  return (
    <div className="ov-card">
      <p className="ov-card-eyebrow">{t(sectionKey)}</p>
      <div className="ov-mover-list">
        {movers.map((m) => {
          const pctText = `${Math.abs(m.delta_pct).toFixed(0)}%`;
          const minText = `${Math.abs(m.delta_min).toFixed(1)}${t("overview.hero_unit_min")}`;
          return (
            <div
              className={`ov-mover-tile ${direction}`}
              key={m.route_code}
            >
              <div className="ov-mover-name">
                {m.route_short_name
                  ? `${m.route_short_name} (${m.route_code})`
                  : m.route_code}
              </div>
              <div className="ov-mover-chips">
                <span className={`ov-chip ${chipClass}`}>
                  {arrow} {pctText}
                </span>
                <span className="ov-chip ov-chip-neutral">{minText}</span>
                {m.streak_weeks >= 2 && (
                  <span className={`ov-chip ${chipClass}`}>
                    {t(streakKey, { count: m.streak_weeks })}
                  </span>
                )}
              </div>
              {m.sparkline_points.length >= 2 && (
                <div className="ov-mover-spark">
                  <InlineSparkline
                    points={m.sparkline_points}
                    width={80}
                    height={24}
                    accent={sparkAccent}
                    forceAccent
                    showLabels={false}
                    showEndDot={false}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
