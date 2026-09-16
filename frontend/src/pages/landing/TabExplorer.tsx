import { useState } from "react";
import { useTranslation } from "react-i18next";
import { HelpCircle, type LucideIcon } from "lucide-react";
import { SidebarNavList } from "../../components/SidebarNavList";
import { SIDEBAR_NAV_ITEMS } from "../../components/Sidebar";
import { useManualExcerpt, type Locale, type TabManualKey } from "./manualExcerpt";

type ExplorerItem = {
  key: TabManualKey;
  labelKey: string;
  previewKey: string;
  Icon: LucideIcon;
};

// A one-line preview blurb per real sidebar tab. The real Sidebar.tsx has no
// subtitle of its own to reuse (its ITEMS carries only a labelKey), so this
// explorer keeps its own short descriptive copy per tab instead.
const PREVIEW_KEY_BY_TAB: Record<(typeof SIDEBAR_NAV_ITEMS)[number]["to"], string> = {
  overview: "nav.overview_subtitle",
  "route-analysis": "nav.analysis_subtitle",
  reports: "landing.explorer.reports_preview",
};

// Same tab set, labels, and icons as the real signed-in sidebar --
// `SIDEBAR_NAV_ITEMS` is imported directly from components/Sidebar.tsx
// rather than duplicated, so this list exists to preview the actual
// product and cannot silently drift from it. Ask has no sidebar subtitle
// of its own (it renders as a standalone CTA there, not a peer nav item),
// so it gets a dedicated landing-only key instead.
const ITEMS: ExplorerItem[] = [
  ...SIDEBAR_NAV_ITEMS.map((item) => ({
    key: item.to,
    labelKey: item.labelKey,
    previewKey: PREVIEW_KEY_BY_TAB[item.to],
    Icon: item.Icon,
  })),
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
