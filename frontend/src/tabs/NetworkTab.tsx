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

  const cols = [
    { key: "agency", label: t("network.col_agency"), help: t("network.help_agency"), num: false },
    { key: "avg", label: t("network.col_avg_delay"), help: t("network.help_avg_delay"), num: true },
    { key: "ontime", label: t("network.col_on_time"), help: t("network.help_on_time"), num: true },
    { key: "samples", label: t("network.col_samples"), help: t("network.help_samples"), num: true },
    { key: "feed", label: t("network.col_feed"), help: t("network.help_feed"), num: true },
    { key: "fresh", label: t("network.col_freshness"), help: t("network.help_freshness"), num: false },
    { key: "coverage", label: t("network.col_coverage"), help: t("network.help_coverage"), num: false },
  ];

  return (
    <div style={{ padding: 24, maxWidth: 1040, margin: "0 auto" }}>
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
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead>
            <tr style={{ color: "var(--text-tertiary)", fontSize: 12, textAlign: "left" }}>
              {cols.map((c) => (
                <th key={c.key} scope="col" title={c.help} style={c.num ? thNum : th}>
                  {c.label}
                </th>
              ))}
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
                    <span title={t("network.help_freshness")} style={{ background: "var(--error-bg)", color: "var(--error-fg)", padding: "2px 8px", borderRadius: 4, fontSize: 12 }}>
                      {t("network.stale_badge")}
                    </span>
                  )}
                </td>
                <td style={td}>
                  {a.data_to == null ? (
                    <span style={{ color: "var(--text-tertiary)" }}>{t("network.no_data_in_range")}</span>
                  ) : (
                    `${a.data_from} – ${a.data_to}`
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
