import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { apiUrl } from "../../api/client";
import { useTimetableRevisionDiff, useTimetableRevisions } from "../../api/hooks";
import type { RevisionDayType, RevisionDayTypeSummary, RevisionStopChange, TimetableRevision } from "../../api/types";
import { useAgencyId } from "../../api/useAgencyId";
import { EmptyState } from "../../components/EmptyState";
import { ErrorBanner } from "../../components/ErrorBanner";
import { SkeletonTable } from "../../components/Skeleton";
import { CsvLink } from "../../components/analysis/CsvLink";
import { SHARED_TABLE, td, th } from "../../components/tableStyles";
import { EM_DASH, formatDate, formatNumber } from "../../utils/format";
import { REVISION_DAY_TYPES, pickRevision, serviceClock } from "./revisionModel";
import "../../styles/focusedAnalysis.css";

/** How many changed stops the table lists before "show all". */
const TABLE_ROWS = 50;

const SIGNED = { signDisplay: "exceptZero" } as const;

function count(n: number | null): string {
  return n == null ? EM_DASH : formatNumber(n);
}

/** One revision's comparison, stop by stop: what each day type's typical day
 *  departs before and after it, and the stops that lost departures, an
 *  earlier last bus, or every departure. The revision is picked by `on`. */
export function RevisionsView() {
  const { t } = useTranslation();
  const id = useAgencyId();
  const [params, setParams] = useSearchParams();
  const list = useTimetableRevisions(id);
  const revision = pickRevision(list.data?.revisions ?? [], params.get("on"));
  const [dayType, setDayType] = useState<RevisionDayType>("weekday");
  const diff = useTimetableRevisionDiff(revision ? id : null, revision?.first_day ?? "", dayType);
  const [showAll, setShowAll] = useState(false);

  if (id == null) return null;
  if (list.isError) return <ErrorBanner error={list.error} onRetry={() => void list.refetch()} />;
  if (list.isPending) return <SkeletonTable />;
  if (revision == null) {
    return <EmptyState title={t("revisions.empty_title")} hint={t("revisions.empty_hint")} />;
  }

  const choose = (on: string) => {
    setShowAll(false);
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("on", on);
      return next;
    });
  };
  const csv = new URLSearchParams({ on: revision.first_day, format: "csv" });

  return (
    <div className="focus-page" style={{ padding: "16px 0" }}>
      <div className="focus-header">
        <label className="focus-muted" style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {t("revisions.pick")}
          <select value={revision.first_day} onChange={(e) => choose(e.target.value)}>
            {list.data.revisions.map((r) => (
              <option key={r.first_day} value={r.first_day}>
                {t("revisions.option", { date: formatDate(r.first_day), version: r.after.version })}
              </option>
            ))}
          </select>
        </label>
        <CsvLink href={apiUrl(`/api/${id}/timetable-revisions/diff?${csv.toString()}`)} />
      </div>
      <RevisionVersions revision={revision} />
      {diff.isError ? (
        <ErrorBanner error={diff.error} onRetry={() => void diff.refetch()} />
      ) : diff.isPending ? (
        <SkeletonTable />
      ) : diff.data.reason === "no_revision" ? (
        <EmptyState title={t("revisions.no_revision_title")} hint={t("revisions.no_revision_hint")} />
      ) : !diff.data.available ? (
        <EmptyState title={t("revisions.no_calendar_title")} hint={t("revisions.no_calendar_hint")} />
      ) : (
        <>
          <DayTypeTable rows={diff.data.day_types} />
          <section>
            <h2>{t("revisions.stops_title")}</h2>
            <div className="focus-tabs" role="group" aria-label={t("revisions.day_type_label")}>
              {REVISION_DAY_TYPES.map((dt) => (
                <button
                  key={dt}
                  type="button"
                  aria-pressed={dt === dayType}
                  onClick={() => {
                    setDayType(dt);
                    setShowAll(false);
                  }}
                >
                  {t(`revisions.day_type.${dt}`)}
                </button>
              ))}
            </div>
            <StopTable
              stops={diff.data.stops}
              known={diff.data.day_types.find((s) => s.day_type === dayType)}
              showAll={showAll}
              onShowAll={() => setShowAll(true)}
            />
          </section>
          <p className="focus-muted">{t("revisions.note")}</p>
        </>
      )}
    </div>
  );
}

function RevisionVersions({ revision }: { revision: TimetableRevision }) {
  const { t } = useTranslation();
  const { before, after } = revision;
  return (
    <p className="focus-muted" style={{ margin: 0 }}>
      {t("revisions.versions", {
        date: formatDate(revision.first_day),
        before: before.version,
        beforeContent: before.content,
        after: after.version,
        afterContent: after.content,
      })}
    </p>
  );
}

function DayTypeTable({ rows }: { rows: RevisionDayTypeSummary[] }) {
  const { t } = useTranslation();
  const day = (iso: string | null) => (iso == null ? EM_DASH : formatDate(iso, { year: false, weekday: true }));
  return (
    <section>
      <h2>{t("revisions.day_types_title")}</h2>
      <div className="focus-table-wrap">
        <table style={SHARED_TABLE}>
          <thead>
            <tr>
              <th style={th()}>{t("revisions.col.day_type")}</th>
              <th style={th()}>{t("revisions.col.typical_days")}</th>
              <th style={th({ align: "right" })}>{t("revisions.col.departures")}</th>
              <th style={th({ align: "right" })}>{t("revisions.col.change")}</th>
              <th style={th({ align: "right" })}>{t("revisions.col.stops_ended")}</th>
              <th style={th({ align: "right" })}>{t("revisions.col.last_earlier")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.day_type}>
                <td style={td()}>{t(`revisions.day_type.${row.day_type}`)}</td>
                <td style={td()}>
                  {day(row.before_date)} → {day(row.after_date)}
                </td>
                <td style={td({ align: "right" })}>
                  {count(row.departures_before)} → {count(row.departures_after)}
                </td>
                <td style={td({ align: "right" })}>
                  {row.departures_before == null || row.departures_after == null
                    ? EM_DASH
                    : formatNumber(row.departures_after - row.departures_before, SIGNED)}
                </td>
                <td style={td({ align: "right" })}>{count(row.stops_ended)}</td>
                <td style={td({ align: "right" })}>{count(row.last_earlier)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.some((row) => row.before_date == null || row.after_date == null) && (
        <p className="focus-muted">{t("revisions.unknown_day_type")}</p>
      )}
    </section>
  );
}

function StopTable({
  stops,
  known,
  showAll,
  onShowAll,
}: {
  stops: RevisionStopChange[];
  known: RevisionDayTypeSummary | undefined;
  showAll: boolean;
  onShowAll: () => void;
}) {
  const { t } = useTranslation();
  if (known == null || known.before_date == null || known.after_date == null) {
    return <p className="focus-muted">{t("revisions.unknown_day_type")}</p>;
  }
  if (stops.length === 0) return <p className="focus-muted">{t("revisions.no_change")}</p>;
  const shown = showAll ? stops : stops.slice(0, TABLE_ROWS);
  return (
    <>
      <div className="focus-table-wrap">
        <table style={SHARED_TABLE}>
          <thead>
            <tr>
              <th style={th()}>{t("revisions.col.stop")}</th>
              <th style={th({ align: "right" })}>{t("revisions.col.departures")}</th>
              <th style={th({ align: "right" })}>{t("revisions.col.lost_gained")}</th>
              <th style={th({ align: "right" })}>{t("revisions.col.first")}</th>
              <th style={th({ align: "right" })}>{t("revisions.col.last")}</th>
              <th style={th()}>{t("revisions.col.hours")}</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((s) => (
              <tr key={s.stop_id}>
                <td style={td()}>
                  {s.stop_name ?? s.stop_id}
                  {s.ended && <span className="focus-muted"> · {t("revisions.ended")}</span>}
                </td>
                <td style={td({ align: "right" })}>
                  {formatNumber(s.before)} → {formatNumber(s.after)}
                </td>
                <td style={td({ align: "right" })}>
                  {t("revisions.lost_gained", { lost: formatNumber(s.lost), gained: formatNumber(s.gained) })}
                </td>
                <td style={td({ align: "right" })}>
                  {serviceClock(s.first_before)} → {serviceClock(s.first_after)}
                </td>
                <td style={td({ align: "right" })}>
                  {serviceClock(s.last_before)} → {serviceClock(s.last_after)}
                  {s.last_shift_min != null && s.last_shift_min !== 0 && (
                    <span className="focus-muted">
                      {" "}
                      {t("revisions.minutes", { value: formatNumber(s.last_shift_min, SIGNED) })}
                    </span>
                  )}
                </td>
                <td style={td()}>
                  {s.hours
                    .map((h) =>
                      t("revisions.hour_change", { hour: h.hour, delta: formatNumber(h.after - h.before, SIGNED) }),
                    )
                    .join(t("revisions.hour_separator"))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!showAll && stops.length > TABLE_ROWS && (
        <div className="focus-actions">
          <button type="button" onClick={onShowAll}>
            {t("revisions.show_all", { count: stops.length })}
          </button>
        </div>
      )}
    </>
  );
}
