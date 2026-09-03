import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  LayoutDashboard,
  Map as MapIcon,
  BarChart3,
  GitCompare,
  History,
  HelpCircle,
  type LucideIcon,
} from "lucide-react";
import { SidebarNavList } from "../../components/SidebarNavList";
import { useManualExcerpt, type Locale, type TabManualKey } from "./manualExcerpt";

type ExplorerItem = {
  key: TabManualKey;
  labelKey: string;
  previewKey: string;
  Icon: LucideIcon;
};

// Same tab set, labels, and icons as the real signed-in sidebar (see
// components/Sidebar.tsx's ITEMS) -- this list exists so a prospective user
// can preview the actual product, not a separate marketing taxonomy that
// could drift from it. `previewKey` reuses each tab's existing one-line
// `nav.*_subtitle` copy; Ask has no sidebar subtitle of its own (it renders
// as a standalone CTA there, not a peer nav item), so it gets a dedicated
// landing-only key instead.
const ITEMS: ExplorerItem[] = [
  { key: "overview", labelKey: "nav.overview", previewKey: "nav.overview_subtitle", Icon: LayoutDashboard },
  { key: "map", labelKey: "nav.map", previewKey: "nav.map_subtitle", Icon: MapIcon },
  { key: "analysis", labelKey: "nav.analysis", previewKey: "nav.analysis_subtitle", Icon: BarChart3 },
  { key: "network", labelKey: "nav.network", previewKey: "nav.network_subtitle", Icon: GitCompare },
  { key: "live", labelKey: "nav.live", previewKey: "nav.live_subtitle", Icon: History },
  { key: "ask", labelKey: "nav.ask", previewKey: "landing.explorer.ask_preview", Icon: HelpCircle },
];

/** The landing page's single navigation/interaction pattern: a vertical
 *  list of the app's real tabs on the left (the same shared `SidebarNavList`
 *  HelpPage's manual sections and the admin architecture page already use
 *  for "click an item, swap a content panel"); selecting one swaps a panel
 *  showing that tab's quick-look preview plus the matching excerpt from the
 *  real user manual. Deliberately the *only* such pattern on the page -- an
 *  earlier version paired this list with a second, differently-behaved
 *  rotating "orbit" card ring for the manual content, which read as two
 *  competing widgets and was dropped in design review. */
export function TabExplorer() {
  const { t, i18n } = useTranslation();
  const resolved = i18n.resolvedLanguage ?? i18n.language ?? "ja";
  const locale: Locale = resolved.startsWith("en") ? "en" : "ja";
  const [selected, setSelected] = useState<TabManualKey>("overview");
  const active = ITEMS.find((item) => item.key === selected) ?? ITEMS[0];
  const { excerpt, isLoading } = useManualExcerpt(locale, selected);

  return (
    <section className="landing-explorer" aria-labelledby="landing-explorer-heading">
      <h2 id="landing-explorer-heading" className="landing-explorer__heading">
        {t("landing.explorer.heading")}
      </h2>
      <div className="landing-explorer__body">
        <SidebarNavList
          ariaLabel={t("landing.explorer.heading")}
          width={220}
          activeKey={selected}
          onSelect={setSelected}
          items={ITEMS.map((item) => ({
            key: item.key,
            label: (
              <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <item.Icon size={16} strokeWidth={1.5} aria-hidden="true" />
                {t(item.labelKey)}
              </span>
            ),
          }))}
        />
        <div className="landing-explorer__panel">
          <h3 className="landing-explorer__panel-title">{t(active.labelKey)}</h3>
          <p className="landing-explorer__panel-preview">{t(active.previewKey)}</p>
          <p className="landing-explorer__panel-excerpt">
            {isLoading ? t("common.loading") : (excerpt ?? t("landing.explorer.excerpt_unavailable"))}
          </p>
        </div>
      </div>
    </section>
  );
}
