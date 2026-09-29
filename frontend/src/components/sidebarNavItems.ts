import { BarChart3, FileText, LayoutDashboard } from "lucide-react";

/** The sidebar's real nav destinations, in the order it lists them.
 *
 *  Lives outside Sidebar.tsx so a consumer can take the list without
 *  importing the component -- a file exporting both is not a component
 *  module Fast Refresh can handle. */
export const SIDEBAR_NAV_ITEMS = [
  { to: "live", labelKey: "nav.live", Icon: LayoutDashboard },
  { to: "analysis", labelKey: "nav.analysis", Icon: BarChart3 },
  { to: "saved", labelKey: "nav.saved", Icon: FileText },
] as const;
