import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { isoDaysBefore, type Scope, type ScopePatch } from "../api/scope";
import "./StillWorking.css";

/** How long a fetch runs before it says it is still working: past the point
 *  where a skeleton alone reads as stuck, well before ten seconds, when
 *  people expect to hear how things are going. */
export const STILL_WORKING_AFTER_MS = 8000;

/** Mount it while a fetch runs; it says nothing at first, then that the work
 *  is still under way. Given the scope, for a period longer than a week it
 *  offers that period's last week, which counts faster. The live region is
 *  in place from the start so the later message is announced. */
export function StillWorking({ scope, update }: { scope?: Scope; update?: (patch: ScopePatch) => void }) {
  const { t } = useTranslation();
  const [late, setLate] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setLate(true), STILL_WORKING_AFTER_MS);
    return () => clearTimeout(timer);
  }, []);
  const weekFrom = scope ? isoDaysBefore(scope.to, 6) : null;
  const narrower = scope && update && weekFrom != null && weekFrom > scope.from ? () => update({ from: weekFrom }) : null;
  return (
    <div role="status" className="still-working">
      {late && (
        <>
          <span>{narrower ? t("common.still_working_period") : t("common.still_working")}</span>
          {narrower && (
            <button type="button" className="btn-ghost" onClick={narrower}>
              {t("common.narrow_to_week")}
            </button>
          )}
        </>
      )}
    </div>
  );
}
