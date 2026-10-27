import { useTranslation } from "react-i18next";
import type { RouteVariant } from "../../api/types";
import { formatNumber } from "../../utils/format";
import { reverseVariant } from "./routeDossier";

/** A share as the picker prints it: whole percent, or one decimal below 1%
 *  so a rare variant does not read as 0%. */
function share(pct: number): string {
  return formatNumber(pct < 1 ? Math.round(pct * 10) / 10 : Math.round(pct));
}

/** Which of a route's stopping patterns the page shows. Each GTFS shape is
 *  one. GTFS direction_id is optional and often empty, and a feed may run
 *  each direction under its own route code, so the variant is what tells a
 *  route's directions apart. Nothing to pick for a route with one. */
export function RouteVariantPicker({ variants, shown, onPick }: {
  variants: RouteVariant[]; shown: string | null; onPick: (shapeId: string) => void;
}) {
  const { t } = useTranslation("design");
  const current = variants.find((v) => v.shape_id === shown);
  if (variants.length < 2 || !current) return null;
  const reverse = reverseVariant(variants, current.shape_id);
  return (
    <div className="route-variant">
      <label>
        {t("variant")}
        <select value={current.shape_id} onChange={(e) => onPick(e.target.value)}>
          {variants.map((v) => (
            <option key={v.shape_id} value={v.shape_id}>
              {t("variantOption", {
                from: v.first_stop_name,
                to: v.last_stop_name,
                stops: formatNumber(v.stop_count),
                share: share(v.share_pct),
              })}
            </option>
          ))}
        </select>
      </label>
      {reverse && <button type="button" className="btn-ghost" onClick={() => onPick(reverse.shape_id)}>{t("reverseDirection")}</button>}
      <p className="focus-muted">{t("variantShare", { share: share(current.share_pct) })}</p>
    </div>
  );
}
