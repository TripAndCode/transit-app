import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ctxToQueryString, useRangeContext } from "../api/rangeContext";
import { useNetworkSummary } from "../api/hooks";
import { Skeleton } from "../components/Skeleton";
import { ErrorBanner } from "../components/ErrorBanner";
import { delayColor } from "../styles/tokens";
import type { NetworkAgencyRow } from "../api/types";

const CLAMP_NOTABLE_PCT = 1; // show a marker when ≥1% of readings were implausible (clamped)

const card: React.CSSProperties = {
  padding: "14px 18px",
  background: "var(--bg-surface)",
  border: "1px solid var(--border-soft)",
  borderRadius: 10,
  marginBottom: 10,
};
const cardTop: React.CSSProperties = { display: "flex", alignItems: "baseline", gap: 10, marginBottom: 8 };
const rankStyle: React.CSSProperties = { fontSize: 12, fontWeight: 700, color: "var(--text-tertiary)", width: 24, flexShrink: 0 };
const agencyNameStyle: React.CSSProperties = { fontSize: 15, fontWeight: 700, flex: 1 };
const delayValStyle: React.CSSProperties = { fontSize: 32, fontWeight: 800, letterSpacing: "-0.025em", fontVariantNumeric: "tabular-nums" };
const delayUnitStyle: React.CSSProperties = { fontSize: 16, fontWeight: 500, color: "var(--text-tertiary)" };
const onTimeStyle: React.CSSProperties = { fontSize: 12, color: "var(--text-secondary)" };
const barRow: React.CSSProperties = { display: "flex", alignItems: "center", gap: 10, marginBottom: 6 };
const barBg: React.CSSProperties = { flex: 1, height: 6, background: "var(--bg-soft)", borderRadius: 3, overflow: "hidden" };
const barFill: React.CSSProperties = { height: "100%", borderRadius: 3 };
const samplesStyle: React.CSSProperties = { fontSize: 11, color: "var(--text-tertiary)", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" };
const secondaryRow: React.CSSProperties = { display: "flex", alignItems: "center", gap: 8, fontSize: 11.5, color: "var(--text-tertiary)", marginBottom: 4 };
const coverageStyle: React.CSSProperties = { fontSize: 11.5, color: "var(--text-tertiary)" };

export function NetworkTab() {
  const { t } = useTranslation();
  const [ctx, update] = useRangeContext();
  const { data, isPending, error, refetch } = useNetworkSummary(ctx);

  // Carry the full current range into each agency's Overview, matching how
  // Sidebar/ReportsTab build agency links (proper encoding; "all" dims omitted).
  const filterQS = ctxToQueryString(ctx);
  const suffix = filterQS ? `?${filterQS}` : "";

  const maxDelay =
    data && data.agencies.length > 0
      ? Math.max(...data.agencies.map((a) => a.avg_delay_min ?? 0))
      : 0;

  function renderCard(a: NetworkAgencyRow, index: number) {
    const showFeedFlag = a.clamp_pct != null && a.clamp_pct > CLAMP_NOTABLE_PCT;
    const showFreshnessFlag = a.is_stale;
    return (
      <div className="network-card" key={a.agency_id} style={card}>
        <div style={cardTop}>
          <span style={rankStyle}>#{index + 1}</span>
          <Link
            to={`/agencies/${a.agency_id}/overview${suffix}`}
            title={t("network.view_agency", { name: a.agency_name })}
            style={{ ...agencyNameStyle, color: "var(--accent)", textDecoration: "none" }}
          >
            {a.agency_name}
          </Link>
          <div style={{ textAlign: "right" }}>
            <div style={delayValStyle} aria-label={t("network.col_avg_delay")}>
              {a.avg_delay_min == null ? (
                "—"
              ) : (
                <span style={{ color: delayColor(a.avg_delay_min) }}>
                  {a.avg_delay_min >= 0 ? "+" : ""}
                  {a.avg_delay_min.toFixed(1)}
                  <span style={delayUnitStyle}>{t("network.delay_unit")}</span>
                </span>
              )}
            </div>
            <div style={onTimeStyle} aria-label={t("network.col_on_time")}>
              {a.on_time_pct == null ? "—" : `${a.on_time_pct.toFixed(1)}%`}
            </div>
          </div>
        </div>
        <div style={barRow}>
          <div style={barBg}>
            <div
              style={{
                ...barFill,
                width: a.avg_delay_min != null && maxDelay > 0 ? `${(a.avg_delay_min / maxDelay) * 100}%` : "0%",
                background: a.avg_delay_min == null ? "transparent" : delayColor(a.avg_delay_min),
              }}
            />
          </div>
          <span style={samplesStyle}>{a.samples.toLocaleString()}</span>
        </div>
        {(showFeedFlag || showFreshnessFlag) && (
          <div style={secondaryRow}>
            {showFeedFlag && (
              <span>
                <span data-testid="clamp-dot" aria-hidden style={{ color: "var(--error-fg)", marginRight: 4 }}>●</span>
                {a.clamp_pct!.toFixed(2)}%
              </span>
            )}
            {showFreshnessFlag && (
              <span title={t("network.help_freshness")} style={{ background: "var(--error-bg)", color: "var(--error-fg)", padding: "2px 8px", borderRadius: 4 }}>
                {t("network.stale_badge")}
              </span>
            )}
          </div>
        )}
        <div style={coverageStyle}>
          {a.data_to == null ? t("network.no_data_in_range") : `${a.data_from} – ${a.data_to}`}
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: 24, maxWidth: 1040, margin: "0 auto" }}>
      <style>{`
        .network-card { transition: background var(--transition); }
        .network-card:hover { background: var(--bg-soft); }
        .network-card a:hover { text-decoration: underline; }
      `}</style>
      <div style={{ fontSize: 12, color: "var(--text-tertiary)", letterSpacing: "0.04em" }}>
        {t("network.eyebrow", { from: ctx.from, to: ctx.to })}
      </div>
      <h1 style={{ fontFamily: "var(--font-display)", fontWeight: 600, fontSize: 22, margin: "4px 0 8px" }}>
        {t("network.title")}
      </h1>
      <p style={{ color: "var(--text-secondary)", fontSize: 14, margin: "0 0 12px", maxWidth: 720, lineHeight: 1.5 }}>
        {t("network.help")}
      </p>
      <details style={{ marginBottom: 16, fontSize: 13, color: "var(--text-secondary)" }}>
        <summary style={{ cursor: "pointer", color: "var(--accent)" }}>{t("network.howto_title")}</summary>
        <ul style={{ margin: "8px 0 0", paddingLeft: 18, lineHeight: 1.7 }}>
          <li><strong>{t("network.col_avg_delay")}</strong> — {t("network.help_avg_delay")}</li>
          <li><strong>{t("network.col_on_time")}</strong> — {t("network.help_on_time")}</li>
          <li><strong>{t("network.col_samples")}</strong> — {t("network.help_samples")}</li>
          <li><strong>{t("network.col_feed")}</strong> — {t("network.help_feed")}</li>
          <li><strong>{t("network.col_freshness")}</strong> — {t("network.help_freshness")}</li>
          <li><strong>{t("network.col_coverage")}</strong> — {t("network.help_coverage")}</li>
        </ul>
      </details>

      <div style={{ display: "flex", gap: 16, marginBottom: 20, fontSize: 13, color: "var(--text-secondary)" }}>
        <label>
          {t("network.from")}{" "}
          <input type="date" value={ctx.from} max={ctx.to} onChange={(e) => update({ from: e.target.value })} />
        </label>
        <label>
          {t("network.to")}{" "}
          <input type="date" value={ctx.to} min={ctx.from} onChange={(e) => update({ to: e.target.value })} />
        </label>
      </div>

      {isPending && <Skeleton height={320} />}
      {error && <ErrorBanner error={error} onRetry={() => refetch()} />}
      {data && data.agencies.length === 0 && (
        <p style={{ color: "var(--text-secondary)" }}>{t("network.empty")}</p>
      )}
      {data && data.agencies.length > 0 && (
        <div data-testid="network-card-list">
          {data.agencies.map((a, i) => renderCard(a, i))}
        </div>
      )}
    </div>
  );
}
