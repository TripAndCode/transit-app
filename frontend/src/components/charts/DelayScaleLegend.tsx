import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { delayScaleSteps } from "./delayScale";
import "./DelayScaleLegend.css";

/** The legend of the shared delay shading: every step, labelled with its
 *  minutes. `noData` adds the `--none` fill a view gives a cell without
 *  observations; `children` adds any other mark the view draws. */
export function DelayScaleLegend({ noData = false, children }: { noData?: boolean; children?: ReactNode }) {
  const { t } = useTranslation();
  return (
    <div className="delay-scale-legend" data-testid="delay-scale-legend">
      <span className="delay-scale-legend__title">{t("delay_scale.title")}</span>
      {delayScaleSteps(t).map((step) => (
        <span key={step.color} className="delay-scale-legend__item">
          <i aria-hidden="true" style={{ background: step.color }} />
          {step.label}
        </span>
      ))}
      {noData && (
        <span className="delay-scale-legend__item">
          <i aria-hidden="true" style={{ background: "var(--none)" }} />
          {t("delay_scale.no_data")}
        </span>
      )}
      {children}
    </div>
  );
}
