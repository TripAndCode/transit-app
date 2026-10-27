import type { TFunction } from "i18next";
import { DELAY_RAMP_STEPS } from "../../styles/tokens";
import { roundTenth } from "../../utils/format";

/** Each step of the shared delay ramp with the minutes it holds, read off
 *  the same cuts `delayRampVar` colours by. */
export function delayScaleSteps(t: TFunction): { color: string; label: string }[] {
  // A cut is a whole or half minute, so it reads as written ("5", "1.5").
  const minutesText = (minutes: number) => String(roundTenth(minutes));
  return DELAY_RAMP_STEPS.map((step, i) => {
    const from = i === 0 ? null : DELAY_RAMP_STEPS[i - 1].below;
    const label =
      from == null
        ? t("delay_scale.below", { to: minutesText(step.below) })
        : Number.isFinite(step.below)
          ? t("delay_scale.between", { from: minutesText(from), to: minutesText(step.below) })
          : t("delay_scale.at_least", { from: minutesText(from) });
    return { color: step.color, label };
  });
}
