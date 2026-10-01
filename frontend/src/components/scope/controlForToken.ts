import type { ComponentType } from "react";
import {
  DaysControl,
  DirControl,
  PeriodControl,
  RoutesControl,
  StopControl,
  TimeControl,
  ToleranceControl,
  type ControlProps,
} from "./scopeControls";
import type { TokenKey } from "./scopePhrases";

/** The control each token opens. Service shares the days control, which
 *  holds the timetable select. */
export const CONTROLS: Record<Exclude<TokenKey, "agency">, ComponentType<ControlProps>> = {
  routes: RoutesControl,
  period: PeriodControl,
  days: DaysControl,
  time: TimeControl,
  service: DaysControl,
  stop: StopControl,
  dir: DirControl,
  tolerance: ToleranceControl,
};
