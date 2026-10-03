import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useTodayRouteSummary } from "../api/hooks";
import { useTopmostEscape } from "../hooks/useFocusTrap";
import { EM_DASH, FILTER_SEPARATOR, formatDate, formatDateTime, formatNumber } from "../utils/format";
import { isToday } from "../utils/threadDateBuckets";

/** The one place that says how recent the agency's data is: the last service
 *  day the reports and charts cover, and the newest live reading. The chip
 *  opens a panel that says the same in full, with the feed's recent volume. */
export function DataFreshness({ agencyId, through }: { agencyId: number; through: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const wrapper = useRef<HTMLDivElement>(null);
  const chip = useRef<HTMLButtonElement>(null);
  const { data } = useTodayRouteSummary(agencyId, { autoRefresh: false });
  const lastReading = data?.latest_captured_at ?? null;

  useTopmostEscape(open, () => {
    setOpen(false);
    chip.current?.focus();
  });

  useEffect(() => {
    if (!open) return;
    function onMouseDown(e: MouseEvent) {
      if (!wrapper.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [open]);

  const analyzed = t("topbar.analyzed_through", { date: formatDate(through, { year: false, weekday: true }) });
  const live =
    lastReading &&
    (isToday(lastReading)
      ? t("topbar.live_at", { time: formatDateTime(lastReading, { timeStyle: "short" }) })
      : t("topbar.last_reading", { when: formatDateTime(lastReading) }));

  return (
    <div ref={wrapper} className="data-freshness">
      <button
        ref={chip}
        type="button"
        className="data-freshness__chip"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen(!open)}
      >
        {live ? [analyzed, live].join(FILTER_SEPARATOR) : analyzed}
      </button>
      {open && (
        <div id={panelId} role="region" aria-label={t("topbar.freshness.title")} className="data-freshness__panel">
          <dl>
            <dt>{t("topbar.freshness.reports")}</dt>
            <dd>{t("topbar.freshness.reports_through", { date: formatDate(through, { weekday: true }) })}</dd>
            <dt>{t("topbar.freshness.live")}</dt>
            <dd>{lastReading ? formatDateTime(lastReading) : data ? t("topbar.freshness.live_none") : EM_DASH}</dd>
            <dt>{t("topbar.freshness.readings")}</dt>
            <dd>{data ? formatNumber(data.raw_samples) : EM_DASH}</dd>
            <dt>{t("topbar.freshness.set_aside")}</dt>
            <dd>{data ? formatNumber(data.clamp_count) : EM_DASH}</dd>
          </dl>
          <p>{t("topbar.freshness.note")}</p>
        </div>
      )}
    </div>
  );
}
