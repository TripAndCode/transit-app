import { useTranslation } from "react-i18next";
import { NavLink } from "react-router-dom";
import { ctxToQueryString, useRangeContext } from "../api/rangeContext";
import { VISIBLE_LENSES, type LensId } from "../routes/analysisRoutes";

/** The Analysis workspace's lens strip. Each link carries only the shared
 *  scope (ctxToQueryString), so lens-local params such as `report` or
 *  `sub_tab` don't leak into a lens that doesn't own them. */
export function LensTabs({ agencyId, active }: { agencyId: number; active: LensId }) {
  const { t } = useTranslation();
  const [ctx] = useRangeContext();
  const qs = ctxToQueryString(ctx);
  return (
    <nav
      aria-label={t("lens.nav_label")}
      style={{ display: "flex", gap: 2, borderBottom: "1px solid var(--border-soft)", overflowX: "auto", marginBottom: 12 }}
    >
      {VISIBLE_LENSES.map((lens) => (
        <NavLink
          key={lens}
          to={`/agencies/${agencyId}/analysis/${lens}${qs ? `?${qs}` : ""}`}
          aria-current={lens === active ? "page" : undefined}
          style={{
            padding: "9px 12px 8px",
            fontSize: "var(--text-sm)",
            whiteSpace: "nowrap",
            textDecoration: "none",
            color: lens === active ? "var(--text-primary)" : "var(--text-secondary)",
            fontWeight: lens === active ? 500 : 400,
            borderBottom: `2px solid ${lens === active ? "var(--accent)" : "transparent"}`,
          }}
        >
          {t(`lens.${lens}`)}
        </NavLink>
      ))}
    </nav>
  );
}
