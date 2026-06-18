import { useTranslation } from "react-i18next";
import { useRangeContext } from "../api/rangeContext";
import { useNetworkSummary } from "../api/hooks";
import { Skeleton } from "../components/Skeleton";
import { ErrorBanner } from "../components/ErrorBanner";
import { delayColor } from "../styles/tokens";

const CLAMP_NOTABLE_PCT = 1; // show a marker when ≥1% of readings were implausible (clamped)

const th: React.CSSProperties = { padding: "6px 10px", fontWeight: 500 };
const thNum: React.CSSProperties = { ...th, textAlign: "right" };
const td: React.CSSProperties = { padding: "8px 10px", color: "var(--text-primary)" };
const tdNum: React.CSSProperties = { ...td, textAlign: "right" };

export function NetworkTab() {
  const { t } = useTranslation();
  const [ctx, update] = useRangeContext();
  const { data, isPending, error, refetch } = useNetworkSummary(ctx);

  return (
    <div style={{ padding: 24, maxWidth: 960, margin: "0 auto" }}>
      <div style={{ fontSize: 12, color: "var(--text-tertiary)", letterSpacing: "0.04em" }}>
        {t("network.eyebrow", { from: ctx.from, to: ctx.to })}
      </div>
      <h1 style={{ fontFamily: "var(--font-display)", fontWeight: 600, fontSize: 22, margin: "4px 0 16px" }}>
        {t("network.title")}
      </h1>

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
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead>
            <tr style={{ color: "var(--text-tertiary)", fontSize: 12, textAlign: "left" }}>
              <th scope="col" style={th}>{t("network.col_agency")}</th>
              <th scope="col" style={thNum}>{t("network.col_avg_delay")}</th>
              <th scope="col" style={thNum}>{t("network.col_on_time")}</th>
              <th scope="col" style={thNum}>{t("network.col_samples")}</th>
              <th scope="col" style={thNum}>{t("network.col_feed")}</th>
              <th scope="col" style={th}>{t("network.col_freshness")}</th>
            </tr>
          </thead>
          <tbody>
            {data.agencies.map((a) => (
              <tr key={a.agency_id} style={{ borderTop: "1px solid var(--border-soft)" }}>
                <td style={td}>{a.agency_name}</td>
                <td style={tdNum}>
                  {a.avg_delay_min == null ? (
                    "—"
                  ) : (
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <span aria-hidden style={{ width: 8, height: 8, borderRadius: "50%", background: delayColor(a.avg_delay_min), flex: "0 0 auto" }} />
                      {a.avg_delay_min.toFixed(1)}
                    </span>
                  )}
                </td>
                <td style={tdNum}>{a.on_time_pct == null ? "—" : `${a.on_time_pct.toFixed(1)}%`}</td>
                <td style={tdNum}>{a.samples.toLocaleString()}</td>
                <td style={tdNum}>
                  {a.clamp_pct == null ? (
                    "—"
                  ) : (
                    <>
                      {a.clamp_pct > CLAMP_NOTABLE_PCT && (
                        <span data-testid="clamp-dot" aria-hidden style={{ color: "var(--error-fg)", marginRight: 4 }}>●</span>
                      )}
                      {a.clamp_pct.toFixed(2)}%
                    </>
                  )}
                </td>
                <td style={td}>
                  {a.is_stale && (
                    <span style={{ background: "var(--error-bg)", color: "var(--error-fg)", padding: "2px 8px", borderRadius: 4, fontSize: 12 }}>
                      {t("network.stale_badge")}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
