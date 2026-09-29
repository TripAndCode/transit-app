import { useTranslation } from "react-i18next";
import { NavLink } from "react-router-dom";
import { ctxToQueryString, useRangeContext } from "../api/rangeContext";
import { VISIBLE_LENSES, type LensId } from "../routes/analysisRoutes";
import { SCREEN_STRIP_STYLE, screenStripLinkStyle } from "./screenStrip";

/** The Analysis workspace's lens strip. Each link carries only the shared
 *  scope (ctxToQueryString), so lens-local params such as `report` or
 *  `sub_tab` don't leak into a lens that doesn't own them. */
export function LensTabs({ agencyId, active }: { agencyId: number; active: LensId }) {
  const { t } = useTranslation();
  const [ctx] = useRangeContext();
  const qs = ctxToQueryString(ctx);
  return (
    <nav aria-label={t("lens.nav_label")} style={SCREEN_STRIP_STYLE}>
      {VISIBLE_LENSES.map((lens) => (
        <NavLink
          key={lens}
          to={`/agencies/${agencyId}/analysis/${lens}${qs ? `?${qs}` : ""}`}
          aria-current={lens === active ? "page" : undefined}
          style={screenStripLinkStyle(lens === active)}
        >
          {t(`lens.${lens}`)}
        </NavLink>
      ))}
    </nav>
  );
}
