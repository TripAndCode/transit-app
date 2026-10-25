import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { useMlEvaluations } from "../api/hooks";
import type { MlAgencyEvaluation, MlEvaluationLatest, MlEvaluationWeek, MlFailure } from "../api/types";
import { ErrorBanner } from "../components/ErrorBanner";
import { Skeleton } from "../components/Skeleton";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { EM_DASH, FILTER_SEPARATOR, fmtRatioPct, formatMinutes } from "../utils/format";

const muted = { color: "var(--text-secondary)" } as const;
const cell = { padding: "6px 10px", textAlign: "left" as const, borderBottom: "1px solid var(--border-soft)" };

function SkillText({ skill }: { skill: number | null }) {
  const { t } = useTranslation();
  if (skill == null) return <>{EM_DASH}</>;
  return <>{t(skill >= 0 ? "model.skill_better" : "model.skill_worse", { value: fmtRatioPct(Math.abs(skill)) })}</>;
}

function failureText(t: TFunction, f: MlFailure, latest: MlEvaluationLatest): string {
  const th = latest.thresholds;
  const values = {
    value: fmtRatioPct(f.value),
    threshold: fmtRatioPct(th.min_skill),
    low: fmtRatioPct(th.coverage_low),
    high: fmtRatioPct(th.coverage_high),
    agencies: f.agencies
      .map((id) => latest.agencies.find((a) => a.agency_id === id)?.agency_name ?? String(id))
      .join(", "),
    code: f.code,
  };
  const known = ["skill_below_min", "agency_worse", "coverage_out_of_range"];
  return t(known.includes(f.code) ? `model.failure_${f.code}` : "model.failure_other", values);
}

function Verdict({ latest }: { latest: MlEvaluationLatest }) {
  const { t } = useTranslation();
  return (
    <Card data-testid="model-verdict">
      <div style={{ fontSize: 18, fontWeight: 600, color: latest.adopted ? "var(--text-primary)" : "var(--text-secondary)" }}>
        {t(latest.adopted ? "model.adopted" : "model.not_adopted")}
      </div>
      <div style={muted}>
        <span>{t("model.run_date", { date: latest.run_date })}</span>
        {FILTER_SEPARATOR}
        <span>
          {latest.serving_model_version
            ? t("model.serving", { version: latest.serving_model_version })
            : t("model.serving_none")}
        </span>
      </div>
      {latest.failures.length > 0 && (
        <div data-testid="model-failures" style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 600 }}>{t("model.failures_title")}</div>
          <ul>
            {latest.failures.map((f) => (
              <li key={`${f.code}-${f.agencies.join("-")}`}>{failureText(t, f, latest)}</li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

function Headline({ latest }: { latest: MlEvaluationLatest }) {
  const { t } = useTranslation();
  const th = latest.thresholds;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
      <Card data-testid="model-skill">
        <div style={muted}>{t("model.skill_label")}</div>
        <div style={{ fontSize: 20, fontWeight: 600 }}>
          <SkillText skill={latest.skill} />
        </div>
        <div style={muted}>{t("model.skill_bar", { value: fmtRatioPct(th.min_skill) })}</div>
      </Card>
      <Card data-testid="model-coverage">
        <div style={muted}>{t("model.coverage_label")}</div>
        <div style={{ fontSize: 20, fontWeight: 600 }}>{fmtRatioPct(latest.interval_coverage)}</div>
        <div style={muted}>
          {t("model.coverage_target", { low: fmtRatioPct(th.coverage_low), high: fmtRatioPct(th.coverage_high) })}
        </div>
      </Card>
    </div>
  );
}

function AgencyRow({ a }: { a: MlAgencyEvaluation }) {
  const { t } = useTranslation();
  return (
    <tr>
      <td style={cell}>
        {a.agency_name ?? a.agency_id}
        {a.forecast_live && <span className="caveat-badge" style={{ marginLeft: 8 }}>{t("model.live")}</span>}
      </td>
      <td style={cell}>{t(`model.status_${a.model_status}`)}</td>
      <td style={cell}>
        <SkillText skill={a.model_status === "b0" ? null : a.skill} />
      </td>
      <td style={cell}>
        {formatMinutes(a.lgbm_mae)} / {formatMinutes(a.b0_mae)}
      </td>
    </tr>
  );
}

function History({ weeks }: { weeks: MlEvaluationWeek[] }) {
  const { t } = useTranslation();
  return (
    <table data-testid="model-history" style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr>
          <th style={cell}>{t("model.col_week")}</th>
          <th style={cell}>{t("model.col_verdict")}</th>
          <th style={cell}>{t("model.col_skill")}</th>
        </tr>
      </thead>
      <tbody>
        {weeks.map((w, i) => {
          const older = weeks[i + 1];
          const changed = older != null && older.point_metric !== w.point_metric;
          return (
            <tr key={w.run_date}>
              <td style={cell}>{w.run_date}</td>
              <td style={cell}>{t(w.adopted ? "model.adopted" : "model.not_adopted")}</td>
              <td style={cell}>
                <SkillText skill={w.overall_skill} />
                {changed && (
                  <div style={muted}>{t("model.metric_changed", { metric: t(`model.metric_${w.point_metric}`, { defaultValue: w.point_metric }) })}</div>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function ModelPage() {
  const { t } = useTranslation();
  const { data, isPending, error, refetch } = useMlEvaluations();
  return (
    <div style={{ maxWidth: 960, margin: "0 auto", padding: "16px", display: "grid", gap: 16 }}>
      <PageHeader title={t("model.title")} subtitle={t("model.subtitle")} />
      {isPending && <Skeleton height={200} />}
      {error && <ErrorBanner error={error} onRetry={() => refetch()} />}
      {data && !data.latest && <p style={muted}>{t("model.empty")}</p>}
      {data?.latest && (
        <>
          {!data.forecast_in_use && <p style={muted}>{t("model.flag_off")}</p>}
          <Verdict latest={data.latest} />
          <Headline latest={data.latest} />
          <Card>
            <h2 style={{ fontSize: 16, marginTop: 0 }}>{t("model.agencies_title")}</h2>
            <table data-testid="model-agencies" style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={cell}>{t("model.col_agency")}</th>
                  <th style={cell}>{t("model.col_status")}</th>
                  <th style={cell}>{t("model.col_skill")}</th>
                  <th style={cell}>{t("model.col_mae")}</th>
                </tr>
              </thead>
              <tbody>
                {data.latest.agencies.map((a) => (
                  <AgencyRow key={a.agency_id} a={a} />
                ))}
              </tbody>
            </table>
          </Card>
          <Card>
            <h2 style={{ fontSize: 16, marginTop: 0 }}>{t("model.history_title")}</h2>
            <History weeks={data.history} />
          </Card>
        </>
      )}
    </div>
  );
}
