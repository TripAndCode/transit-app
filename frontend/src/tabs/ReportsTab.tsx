import { useNavigate, useParams } from "react-router-dom";
import { useReport, useReports } from "../api/hooks";
import { useRangeContext } from "../api/rangeContext";
import { relativeTime } from "../utils/relativeTime";
import { TabFilterBar } from "../components/TabFilterBar";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { Skeleton } from "../components/Skeleton";

const REPORT_LABEL: Record<string, string> = {
  ranking: "遅延ランキング",
  ranking_best: "定時運行ランキング",
  on_time: "定時率",
  worst_5min: "5分以上遅延",
  trend: "トレンド",
  compare_ranking: "比較ランキング",
  dow_weekday: "平日傾向",
  dow_weekend: "週末傾向",
};

export function ReportsTab() {
  const { agencyId, reportType } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const navigate = useNavigate();
  const [ctx] = useRangeContext();
  const list = useReports(id);
  const detail = useReport(id, reportType ?? null, ctx);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <TabFilterBar />
      <div style={{ display: "flex", gap: 16, flex: 1, minHeight: 0 }}>
      <div style={{ width: 280, flexShrink: 0 }}>
        <h3 style={{ marginTop: 0, fontSize: 14, color: "var(--text-secondary)" }}>レポート一覧</h3>
        {list.error && <ErrorBanner error={list.error} onRetry={() => list.refetch()} />}
        {list.isLoading && [...Array(6)].map((_, i) => (
          <Skeleton key={i} height={48} style={{ marginBottom: 6 }} />
        ))}
        {list.data && list.data.length === 0 && <EmptyState title="レポートがありません" />}
        {list.data?.map((r) => {
          const active = r.report_type === reportType;
          return (
            <div
              key={r.report_type}
              onClick={() => navigate(`/agencies/${id}/reports/${r.report_type}`)}
              style={{
                padding: "10px 12px",
                marginBottom: 4,
                background: active ? "var(--accent-soft)" : "var(--bg-surface)",
                border: "1px solid var(--border-soft)",
                borderRadius: "var(--radius)",
                cursor: "pointer",
              }}
            >
              <div style={{ fontWeight: 500 }}>{REPORT_LABEL[r.report_type] ?? r.report_type}</div>
              <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
                {relativeTime(r.rendered_at)}
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        {!reportType && (
          <EmptyState title="レポートを選択してください" />
        )}
        {reportType && detail.error && (
          <ErrorBanner error={detail.error} onRetry={() => detail.refetch()} />
        )}
        {reportType && detail.isLoading && <Skeleton height={400} />}
        {detail.data && (
          <div>
            <h2 style={{ marginTop: 0 }}>{REPORT_LABEL[detail.data.report_type] ?? detail.data.report_type}</h2>
            <div style={{ color: "var(--text-tertiary)", fontSize: 13, marginBottom: 16 }}>
              生成: {relativeTime(detail.data.rendered_at)}
            </div>
            <pre
              style={{
                background: "var(--bg-surface)",
                border: "1px solid var(--border-soft)",
                borderRadius: "var(--radius)",
                padding: 16,
                whiteSpace: "pre-wrap",
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                fontSize: 13,
                lineHeight: 1.7,
                maxWidth: 920,
              }}
            >
              {detail.data.text}
            </pre>
            {detail.data.rows.length > 0 && (
              <details style={{ marginTop: 16 }}>
                <summary style={{ cursor: "pointer", color: "var(--text-secondary)" }}>
                  ライブ再実行 ({detail.data.rows.length}件)
                </summary>
                <RowsTable rows={detail.data.rows as Record<string, unknown>[]} />
              </details>
            )}
          </div>
        )}
      </div>
      </div>
    </div>
  );
}

function RowsTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (rows.length === 0) return null;
  const keys = Object.keys(rows[0]);
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 12, fontSize: 13 }}>
      <thead>
        <tr style={{ background: "var(--bg-soft)" }}>
          {keys.map((k) => (
            <th key={k} style={{ padding: "6px 10px", textAlign: "left", color: "var(--text-secondary)", fontWeight: 500 }}>{k}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} style={{ borderTop: "1px solid var(--border-soft)" }}>
            {keys.map((k) => (
              <td key={k} style={{ padding: "6px 10px" }}>{formatCell(r[k])}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function formatCell(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}
