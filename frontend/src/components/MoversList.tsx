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
  const accent = direction === "worse" ? "#b45309" : "#166534";
  const arrowClass = direction === "worse" ? "ov-delta-up" : "ov-delta-down";

  return (
    <div>
      <div className="ov-label">{t(sectionKey)}</div>
      {movers.map((m) => (
        <p className="ov-mover" key={m.route_code} style={{ marginBottom: 12 }}>
          <span className="name">
            {m.route_short_name ? `${m.route_short_name} (${m.route_code})` : m.route_code}
          </span>{" "}
          <span className={arrowClass}>
            {direction === "worse" ? "+" : "▼ "}
            {Math.abs(m.delta_pct).toFixed(0)}%
          </span>
          {m.streak_weeks >= 2 && (
            <span className={`ov-streak ${direction === "better" ? "good" : ""}`}>
              {" "}
              {t(streakKey, { count: m.streak_weeks })}
            </span>
          )}
          {m.sparkline_points.length >= 2 && (
            <>
              {" "}
              <InlineSparkline points={m.sparkline_points} width={60} height={16} accent={accent} />
            </>
          )}
        </p>
      ))}
    </div>
  );
}
