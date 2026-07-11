import { Link, NavLink, useParams } from "react-router-dom";
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
      <Link
        to="/"
        style={{
          textDecoration: "none",
          color: "var(--text-primary)",
          display: "flex",
          flexDirection: "column",
          lineHeight: 1.1,
        }}
      >
        <h1
          style={{
            fontFamily: "var(--font-display)",
            fontWeight: 600,
            fontSize: 20,
            margin: 0,
            letterSpacing: "0.01em",
          }}
        >
          {t("header.app_title")}
        </h1>
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 11,
            color: "var(--text-tertiary)",
            marginTop: 2,
            letterSpacing: "0.04em",
          }}
        >
          {t("header.app_tagline")}
        </span>
      </Link>
      <div style={{ flex: 1, display: "flex", justifyContent: "center" }}>
        <AgencyPicker />
      </div>
      <div style={{ display: "flex", gap: 8 }}>
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
