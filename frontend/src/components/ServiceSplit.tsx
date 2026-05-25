// frontend/src/components/ServiceSplit.tsx
import { useTranslation } from "react-i18next";

type Props = { service_split: Record<string, number> };

export function ServiceSplit({ service_split }: Props) {
  const { t } = useTranslation();
  const keys = Object.keys(service_split);
  if (keys.length === 0) return null;

  const values = keys.map((k) => service_split[k]);
  const maxVal = Math.max(...values, 0.0001); // avoid div-by-zero

  // For the "diff" annotation we need top two values when there are at least 2.
  let diffNode: React.ReactNode = null;
  if (keys.length >= 2) {
    const sorted = [...values].sort((a, b) => b - a);
    const diff = sorted[0] - sorted[1];
    const pct = sorted[1] > 0 ? (diff / sorted[1]) * 100 : 0;
    if (diff > 0) {
      diffNode = (
        <p className="ov-svc-diff">
          {t("overview.service_split.diff", {
            diff: diff.toFixed(1),
            pct: pct.toFixed(0),
          })}
        </p>
      );
    }
  }

  return (
    <div className="ov-card">
      <p className="ov-card-eyebrow">{t("overview.section_service_split")}</p>
      <div className="ov-svc-list">
        {keys.map((k) => {
          const v = service_split[k];
          const pctOfMax = Math.max(0, Math.min(100, (v / maxVal) * 100));
          return (
            <div className="ov-svc-row" key={k}>
              <div className="ov-svc-head">
                <span className="ov-svc-label">
                  {t(`overview.service_split_label.${k}`, { defaultValue: k })}
                </span>
                <span className="ov-svc-num ov-anim-fade">
                  {v.toFixed(1)}
                  {t("overview.hero_unit_min")}
                </span>
              </div>
              <div className="ov-svc-track">
                <div
                  className="ov-svc-fill ov-anim-grow-x"
                  style={{ width: `${pctOfMax}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
      {diffNode}
    </div>
  );
}
