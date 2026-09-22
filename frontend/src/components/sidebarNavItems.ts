import { BarChart3, CalendarRange, FileText, GitCompare, LayoutDashboard } from "lucide-react";

/** The sidebar's real nav destinations -- exported so the landing page's
 *  preview mockups (`pages/landing/PreviewSidebar.tsx`, and
 *  `DashboardPreview.tsx` for its auto-advance order) import this array
 *  instead of maintaining their own copy, so the marketing preview's tab
 *  set/labels cannot drift from the real, signed-in nav. */
export const SIDEBAR_NAV_ITEMS = [
  { to: "operations", labelKey: "design:overview", Icon: LayoutDashboard },
  { to: "period-overview", labelKey: "design:period_overview", Icon: CalendarRange },
  { to: "route-analysis", labelKey: "design:analysis", Icon: BarChart3 },
  { to: "network", labelKey: "network.title", Icon: GitCompare },
  { to: "reports", labelKey: "design:reports", Icon: FileText },
] as const;
