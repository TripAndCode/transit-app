import { Activity, Clock, FileText, GitCompare, Radio, Route, SearchCheck } from "lucide-react";
import type { Destination } from "../routes/destinations";

/** The sidebar's destinations, in the order it lists them.
 *
 *  Lives outside Sidebar.tsx so a consumer can take the list without
 *  importing the component -- a file exporting both is not a component
 *  module Fast Refresh can handle. */
export const SIDEBAR_NAV_ITEMS = [
  { to: "pulse", labelKey: "nav.pulse", Icon: Activity },
  { to: "routes", labelKey: "nav.routes", Icon: Route },
  { to: "time", labelKey: "nav.time", Icon: Clock },
  { to: "why", labelKey: "nav.why", Icon: SearchCheck },
  { to: "compare", labelKey: "nav.compare", Icon: GitCompare },
  { to: "live", labelKey: "nav.live", Icon: Radio },
  { to: "reports", labelKey: "nav.reports", Icon: FileText },
] as const satisfies readonly { to: Destination; labelKey: string; Icon: unknown }[];
