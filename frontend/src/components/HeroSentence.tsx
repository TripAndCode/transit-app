// frontend/src/components/HeroSentence.tsx
import { useTranslation } from "react-i18next";

import type { OverviewHeadline } from "../api/types";
import { InlineSparkline } from "./InlineSparkline";

type Props = {
  headline: OverviewHeadline;
  sparkline_points: number[];
  range: { from: string; to: string };
};

export function HeroSentence({ headline, sparkline_points, range }: Props) {
  const { t } = useTranslation();
  const fmt = (n: number | null) => (n == null ? "—" : n.toFixed(1));

  return (
    <div>
      <div className="ov-eyebrow">
        {t("overview.eyebrow", { from: range.from, to: range.to })}
      </div>
      <h1 className="ov-hero">
        {t("overview.hero_avg_prefix")}{" "}
        <strong className="ov-anim-fade">
          {fmt(headline.avg_min)}
          {t("overview.hero_unit_min")}
        </strong>
        {". "}
        {headline.delta_min != null ? (
          <>
            <br />
            {t("overview.hero_compared")}{" "}
            <span
              className={`ov-anim-delta ${
                headline.delta_min > 0 ? "ov-delta-up" : "ov-delta-down"
              }`}
            >
              {headline.delta_min > 0 ? "▲" : "▼"} {Math.abs(headline.delta_min).toFixed(1)}
              {t("overview.hero_unit_min")}
              {headline.delta_pct != null
                ? ` (${headline.delta_pct > 0 ? "+" : ""}${headline.delta_pct.toFixed(0)}%)`
                : ""}
            </span>{" "}
            <InlineSparkline points={sparkline_points} />
          </>
        ) : (
          <>
            <br />
            <span className="ov-sub">{t("overview.hero_no_baseline")}</span>
          </>
        )}
      </h1>
      <p className="ov-sub">{t("overview.hero_samples", { count: headline.samples })}</p>
    </div>
  );
}
