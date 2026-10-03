import { lazy } from "react";
import { TIME_REPORT_TYPES } from "../routes/destinations";
import { loadAnalysisTab } from "../routes/lazyTabs";

const AnalysisTab = lazy(loadAnalysisTab);

export function TimeTab() {
  return <AnalysisTab reportTypes={TIME_REPORT_TYPES} />;
}
