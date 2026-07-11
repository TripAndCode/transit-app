import { NavLink, useParams } from "react-router-dom";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AgencyPicker } from "./AgencyPicker";
import { SettingsDrawer } from "./SettingsDrawer";
import { HeaderUserMenu } from "./HeaderUserMenu";
import { LocaleToggle } from "./LocaleToggle";
import { ThemeToggle } from "./ThemeToggle";
import { ctxToQueryString, useRangeContext } from "../api/rangeContext";

export function Header() {
  const { t } = useTranslation();
  const { agencyId } = useParams();
  const [ctx] = useRangeContext();
  const filterQS = ctxToQueryString(ctx);
  const suffix = filterQS ? `?${filterQS}` : "";
  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <header
      style={{
        height: 56,
        background: "var(--bg-surface)",
        borderBottom: "1px solid var(--border-soft)",
        display: "flex",
        alignItems: "center",
        padding: "0 24px",
        gap: 24,
      }}
    >
      <div style={{ display: "flex", alignItems: "center" }}>
        <AgencyPicker />
      </div>
      <div style={{ display: "flex", gap: 8, flex: 1, justifyContent: "flex-end", alignItems: "center" }}>
        {/* Live moved here from the sidebar (artifact-parity Branch 2) — the
            mockup's sidebar has no equivalent screen, so this preserves the
            feature at a secondary access point instead of removing it.
            Network moved the other way (into the sidebar). Gated on agencyId
            like Sidebar's agency-scoped items — unlike the old Network link
            here, which rendered unconditionally. */}
        {agencyId && (
          <NavLink
            to={`/agencies/${agencyId}/live${suffix}`}
            style={({ isActive }) => ({
              alignSelf: "center",
              fontSize: 13,
              textDecoration: "none",
              color: isActive ? "var(--accent)" : "var(--text-secondary)",
              fontWeight: isActive ? 600 : 400,
            })}
          >
            {t("nav.live")}
          </NavLink>
        )}
        <HeaderUserMenu />
        <LocaleToggle />
        <ThemeToggle />
        <button
          type="button"
          onClick={() => setSettingsOpen(true)}
          aria-label={t("header.settings_aria")}
          style={{
            background: "transparent",
            color: "var(--text-primary)",
            border: "1px solid var(--border-subtle)",
            padding: "6px 10px",
            borderRadius: 4,
          }}
        >
          ⚙
        </button>
      </div>
      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </header>
  );
}
