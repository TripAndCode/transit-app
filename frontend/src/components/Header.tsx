import { Link, useSearchParams } from "react-router-dom";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AgencyPicker } from "./AgencyPicker";
import { SettingsDrawer } from "./SettingsDrawer";
import { HeaderUserMenu } from "./HeaderUserMenu";
import { LocaleToggle } from "./LocaleToggle";
import { AgencyForm } from "../admin/AgencyForm";

export function Header() {
  const { t } = useTranslation();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);
  const [params] = useSearchParams();
  const isAdmin = params.get("admin") === "1";

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
        <Link
          to="/network"
          style={{ alignSelf: "center", fontSize: 13, color: "var(--text-secondary)", textDecoration: "none" }}
        >
          {t("nav.network")}
        </Link>
        <HeaderUserMenu />
        <LocaleToggle />
        {isAdmin && (
          <button
            type="button"
            onClick={() => setAdminOpen(true)}
            style={{
              background: "var(--accent)",
              color: "#fff",
              border: "none",
              padding: "6px 14px",
              borderRadius: 4,
            }}
          >
            {`+ ${t("header.new_agency")}`}
          </button>
        )}
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
      {adminOpen && <AgencyForm onClose={() => setAdminOpen(false)} />}
    </header>
  );
}
