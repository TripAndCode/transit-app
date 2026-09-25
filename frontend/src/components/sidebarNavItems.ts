import { BarChart3, CalendarRange, FileText, GitCompare, LayoutDashboard } from "lucide-react";

/** The sidebar's real nav destinations, in the order it lists them.
 *
 *  Lives outside Sidebar.tsx so a consumer can take the list without
 *  importing the component -- a file exporting both is not a component
 *  module Fast Refresh can handle. */
export const SIDEBAR_NAV_ITEMS = [
  { to: "operations", labelKey: "design:operations", Icon: LayoutDashboard },
  { to: "period-overview", labelKey: "design:period_overview", Icon: CalendarRange },
  { to: "route-analysis", labelKey: "design:analysis", Icon: BarChart3 },
  { to: "network", labelKey: "network.title", Icon: GitCompare },
  { to: "reports", labelKey: "design:reports", Icon: FileText },
] as const;
