import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { apiUrl } from "../../api/client";
import { useTransferPairs, useTransferReliability } from "../../api/hooks";
import type { TransferPairSide, TransferReliability, TransferTally } from "../../api/types";
import { useAgencyId } from "../../api/useAgencyId";
import { EmptyState } from "../../components/EmptyState";
import { ErrorBanner } from "../../components/ErrorBanner";
import { SkeletonTable } from "../../components/Skeleton";
import { CsvLink } from "../../components/analysis/CsvLink";
import { SHARED_TABLE, td, th } from "../../components/tableStyles";
import { EM_DASH, formatDate, formatDateRange, formatNumber } from "../../utils/format";
import { minutes, outcomeWidths, pickDayType, pickPair } from "./transferModel";
import "../../styles/focusedAnalysis.css";

const PERCENT = { style: "percent", maximumFractionDigits: 0 } as const;

// Held reads as the accent, missed as the calm warning tone, and unobserved
// as the empty track: a connection nobody saw is not drawn as either outcome.
const OUTCOME_COLOURS = {
  held: "var(--accent)",
  missed: "var(--color-warning)",
  unobserved: "var(--track-bg)",
} as const;

function share(value: number | null): string {
  return value == null ? EM_DASH : formatNumber(value, PERCENT);
}

/** Compare's transfers board: for one defined hub transfer, how often the
 *  planned connection held, by hour and by day, how long riders waited
 *  against the survey's 5/10/20-minute tolerances, and the planned buffer
 *  that would have held 95% of connections. The pair is picked by `pair`
 *  and its day type by `pair_day`. */
export function TransfersView() {
  const { t } = useTranslation();
  const id = useAgencyId();
  const [params, setParams] = useSearchParams();
  const list = useTransferPairs(id);
  const pair = pickPair(list.data?.pairs ?? [], params.get("pair"));
  const dayType = pair == null ? null : pickDayType(pair, params.get("pair_day"));
  const report = useTransferReliability(pair == null ? null : id, pair?.id ?? null, dayType);

  if (id == null) return null;
  if (list.isError) return <ErrorBanner error={list.error} onRetry={() => void list.refetch()} />;
  if (list.isPending) return <SkeletonTable />;
  if (pair == null || dayType == null) {
    return <EmptyState title={t("transfers.empty_title")} hint={t("transfers.empty_hint")} />;
  }

  const choose = (key: string, value: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set(key, value);
      return next;
    });
  const csv = new URLSearchParams({ day_type: dayType, format: "csv" });

  return (
    <div className="focus-page" style={{ padding: "16px 0" }}>
      <div className="focus-header">
        <label className="focus-muted" style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {t("transfers.pick")}
          <select value={pair.id} onChange={(e) => choose("pair", e.target.value)}>
            {list.data.pairs.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <CsvLink href={apiUrl(`/api/${id}/transfer-pairs/${pair.id}/reliability?${csv.toString()}`)} />
      </div>
      <p className="focus-muted" style={{ margin: 0 }}>
        {t("transfers.pair_line", {
          from: sideLabel(pair.arriving),
          to: sideLabel(pair.departing),
          walk: minutes(pair.walk_sec),
        })}
      </p>
      {pair.day_types.length > 1 && (
        <div className="focus-tabs" role="group" aria-label={t("transfers.day_type_label")}>
          {pair.day_types.map((d) => (
            <button key={d} type="button" aria-pressed={d === dayType} onClick={() => choose("pair_day", d)}>
              {t(`transfers.day_type.${d}`)}
            </button>
          ))}
        </div>
      )}
      {report.isError ? (
        <ErrorBanner error={report.error} onRetry={() => void report.refetch()} />
      ) : report.isPending ? (
        <SkeletonTable />
      ) : (
        <ReliabilityBody data={report.data} />
      )}
    </div>
  );
}

function sideLabel(side: TransferPairSide): string {
  const stops = [...new Set(side.stop_names.map((name, i) => name ?? side.stop_ids[i]))].join(" / ");
  return `${side.agency_name} ${side.route_name ?? side.route_code} (${stops})`;
}

function ReliabilityBody({ data }: { data: TransferReliability }) {
  const { t } = useTranslation();
  const dayTypeName = t(`transfers.day_type.${data.day_type}`);
  return (
    <>
      <p className="focus-muted" style={{ margin: 0 }}>
        {t("transfers.period", { range: formatDateRange(data.from_date, data.to_date), dayType: dayTypeName })}
      </p>
      <section aria-labelledby="transfers-summary">
        <h2 id="transfers-summary">{t("transfers.summary_title")}</h2>
        <dl style={{ display: "flex", gap: 28, flexWrap: "wrap", margin: 0 }}>
          <Figure label={t("transfers.planned")} value={formatNumber(data.total.planned)} />
          <Figure label={t("transfers.held")} value={formatNumber(data.total.held)} />
          <Figure label={t("transfers.missed")} value={formatNumber(data.total.missed)} />
          <Figure label={t("transfers.unobserved")} value={formatNumber(data.total.unobserved)} />
          <Figure label={t("transfers.held_share")} value={share(data.held_share)} />
        </dl>
        {data.no_onward > 0 && <p className="focus-muted">{t("transfers.no_onward", { count: data.no_onward })}</p>}
        {data.buffer_min != null ? (
          <p data-testid="transfers-recommendation">
            {t("transfers.recommendation", {
              minutes: data.buffer_min,
              share: formatNumber(data.buffer_share, PERCENT),
              dayType: t(`transfers.day_type_phrase.${data.day_type}`),
            })}{" "}
            <span className="focus-muted">{t("transfers.feed_based")}</span>
          </p>
        ) : (
          <p className="focus-muted">{t("transfers.withheld", { min: data.min_samples })}</p>
        )}
      </section>
      <HourBars data={data} />
      <WaitBands data={data} />
      <WorstDays data={data} />
      <p className="focus-muted">{t("transfers.note")}</p>
      {data.no_plan_days.length > 0 && (
        <p className="focus-muted">{t("transfers.no_plan_days", { count: data.no_plan_days.length })}</p>
      )}
    </>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="focus-muted">{label}</dt>
      <dd style={{ margin: 0, fontSize: "var(--text-xl)", fontVariantNumeric: "tabular-nums" }}>{value}</dd>
    </div>
  );
}

function OutcomeBar({ tally, label }: { tally: TransferTally; label: string }) {
  const widths = outcomeWidths(tally);
  return (
    <div
      role="img"
      aria-label={label}
      style={{ display: "flex", height: 10, borderRadius: 999, overflow: "hidden", background: "var(--track-bg)" }}
    >
      {(["held", "missed", "unobserved"] as const).map((k) => (
        <span key={k} style={{ width: `${widths[k]}%`, background: OUTCOME_COLOURS[k] }} />
      ))}
    </div>
  );
}

function HourBars({ data }: { data: TransferReliability }) {
  const { t } = useTranslation();
  return (
    <section aria-labelledby="transfers-hours">
      <h2 id="transfers-hours">{t("transfers.by_hour_title")}</h2>
      {data.by_hour.length === 0 ? (
        <p className="focus-muted">{t("transfers.no_connections")}</p>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "auto minmax(120px, 1fr) auto", gap: "8px 12px", alignItems: "center" }}>
          {data.by_hour.map((h) => {
            const counts = t("transfers.counts", { held: h.held, missed: h.missed, unobserved: h.unobserved });
            return (
              <div key={h.hour} style={{ display: "contents" }}>
                <span className="focus-muted">{t("transfers.hour", { hour: h.hour })}</span>
                <OutcomeBar tally={h} label={counts} />
                <span className="focus-muted" style={{ opacity: h.small_sample ? 0.7 : 1 }}>
                  {counts}
                </span>
              </div>
            );
          })}
        </div>
      )}
      <p className="focus-muted">{t("transfers.legend")}</p>
    </section>
  );
}

function WaitBands({ data }: { data: TransferReliability }) {
  const { t } = useTranslation();
  return (
    <section aria-labelledby="transfers-waits">
      <h2 id="transfers-waits">{t("transfers.wait_title")}</h2>
      {data.wait_bands == null || data.wait_p50_sec == null || data.wait_p90_sec == null ? (
        <p className="focus-muted">{t("transfers.wait_withheld", { n: data.waits, min: data.min_samples })}</p>
      ) : (
        <>
          <p style={{ margin: "0 0 8px" }}>
            {t("transfers.wait_percentiles", { p50: minutes(data.wait_p50_sec), p90: minutes(data.wait_p90_sec) })}
          </p>
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 8, maxWidth: 520 }}>
            {data.wait_bands.map((band) => (
              <li key={band.max_sec} style={{ display: "grid", gridTemplateColumns: "8em 1fr 4em", gap: 12, alignItems: "center" }}>
                <span className="focus-muted">{t("transfers.within", { minutes: minutes(band.max_sec) })}</span>
                <span style={{ height: 8, borderRadius: 999, background: "var(--track-bg)", overflow: "hidden" }}>
                  <span style={{ display: "block", height: "100%", width: `${band.share * 100}%`, background: "var(--accent)" }} />
                </span>
                <span style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{share(band.share)}</span>
              </li>
            ))}
          </ul>
          <p className="focus-muted">{t("transfers.wait_note", { n: data.waits })}</p>
        </>
      )}
    </section>
  );
}

function WorstDays({ data }: { data: TransferReliability }) {
  const { t } = useTranslation();
  return (
    <section aria-labelledby="transfers-worst">
      <h2 id="transfers-worst">{t("transfers.worst_title")}</h2>
      {data.worst_days.length === 0 ? (
        <p className="focus-muted">{t("transfers.no_missed")}</p>
      ) : (
        <div className="focus-table-wrap">
          <table style={SHARED_TABLE}>
            <thead>
              <tr>
                <th style={th()}>{t("transfers.col.day")}</th>
                <th style={th({ align: "right" })}>{t("transfers.missed")}</th>
                <th style={th({ align: "right" })}>{t("transfers.held")}</th>
                <th style={th({ align: "right" })}>{t("transfers.unobserved")}</th>
                <th style={th({ align: "right" })}>{t("transfers.held_share")}</th>
              </tr>
            </thead>
            <tbody>
              {data.worst_days.map((d) => (
                <tr key={d.day}>
                  <td style={td()}>{formatDate(d.day, { weekday: true })}</td>
                  <td style={td({ align: "right" })}>{formatNumber(d.missed)}</td>
                  <td style={td({ align: "right" })}>{formatNumber(d.held)}</td>
                  <td style={td({ align: "right" })}>{formatNumber(d.unobserved)}</td>
                  <td style={td({ align: "right" })}>{share(d.held_share)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
