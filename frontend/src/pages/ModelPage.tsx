import { useState } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { useAgencies, useMlEvaluations } from "../api/hooks";
import { readLastAgency } from "../api/lastAgency";
import type { MlAgencyEvaluation, MlEvaluationLatest, MlEvaluationWeek, MlFailure } from "../api/types";
import { ErrorBanner } from "../components/ErrorBanner";
import { Skeleton } from "../components/Skeleton";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { MOBILE_BREAKPOINT_QUERY, useMediaQuery } from "../hooks/useMediaQuery";
import { EM_DASH, FILTER_SEPARATOR, fixedTenth, fmtRatioPct, roundTenth } from "../utils/format";
import "./ModelPage.css";

const muted = { color: "var(--text-secondary)" } as const;
const MAE_SEPARATOR = " / "; // i18n-ignore: locale-neutral separator
const cell = { padding: "6px 10px", textAlign: "left" as const, borderBottom: "1px solid var(--border-soft)" };

function SkillText({ skill }: { skill: number | null }) {
  const { t } = useTranslation();
  if (skill == null) return <>{EM_DASH}</>;
  return (
    <span className="model-nowrap">
      {t(skill >= 0 ? "model.skill_better" : "model.skill_worse", { value: fmtRatioPct(Math.abs(skill)) })}
    </span>
  );
}

/** A mean error held in minutes, read in whole seconds: at a tenth of a
 *  minute the model's error and the usual estimate's often print alike. */
function maeSeconds(t: TFunction, minutes: number | null): string {
  if (minutes == null || !isFinite(minutes)) return EM_DASH;
  return t("common.duration_sec", { sec: Math.round(minutes * 60) });
}

/** Whether the agency stayed on the usual estimate throughout. Its model
 *  figures are then that estimate's own error plus the model's only where
 *  the estimate had no history, not how the model did, so the page shows
 *  neither the model's skill nor its error for it, as the API withholds the
 *  skill. */
function onUsualEstimate(a: MlAgencyEvaluation): boolean {
  return a.model_status === "b0";
}

function MaeText({ a }: { a: MlAgencyEvaluation }) {
  const { t } = useTranslation();
  const b0 = maeSeconds(t, a.b0_mae);
  const b0Text = <span className="model-nowrap">{t("model.mae_b0", { value: b0 })}</span>;
  if (onUsualEstimate(a)) return b0Text;
  return (
    <>
      <span className="model-nowrap">{t("model.mae_model", { value: maeSeconds(t, a.lgbm_mae) })}</span>
      {MAE_SEPARATOR}
      {b0Text}
    </>
  );
}

/** Where the skill stands against the adoption bar, in percentage points
 *  between the two figures as printed, so the margin agrees with them. A
 *  margin that rounds to zero is worded by which side of the bar the skill
 *  fell, which decides adoption. */
function barMarginText(t: TFunction, skill: number | null, bar: number | null): string | null {
  if (skill == null || bar == null) return null;
  const points = roundTenth(roundTenth(skill * 100) - roundTenth(bar * 100));
  if (points === 0) return t(skill >= bar ? "model.skill_margin_reached" : "model.skill_margin_short");
  return t(points > 0 ? "model.skill_margin_above" : "model.skill_margin_below", { points: fixedTenth(Math.abs(points)) });
}

function OriginSpread({ spread }: { spread: MlEvaluationLatest["origin_skill"] }) {
  const { t } = useTranslation();
  const { count, min, median, max } = spread;
  if (!count || min == null || median == null || max == null) return null;
  return (
    <>
      <div style={muted}>
        {t("model.origin_spread", { count, min: fmtRatioPct(min), median: fmtRatioPct(median), max: fmtRatioPct(max) })}
      </div>
      {roundTenth(min * 100) < 0 && <div style={muted}>{t("model.origin_spread_negative")}</div>}
    </>
  );
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

/** The agency the visitor opened this page from (`from`, set by the links
 *  that lead here), else the last one chosen, whichever names a known agency:
 *  the page belongs to no agency, so without it the way back would be the
 *  picker. */
function BackToAgency() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const [stored] = useState(readLastAgency);
  const from = params.get("from");
  const agencies = useAgencies().data;
  const agency =
    (from != null ? agencies?.find((a) => a.agency_id === Number(from)) : undefined) ??
    agencies?.find((a) => a.agency_id === stored);
  if (!agency) return null;
  return (
    <Link to={`/agencies/${agency.agency_id}/pulse`} style={{ justifySelf: "start", fontSize: "var(--text-sm)" }}>
      {t("model.back_to_agency", { agency: agency.agency_name })}
    </Link>
  );
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
  const margin = barMarginText(t, latest.skill, th.min_skill);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
      <Card data-testid="model-skill">
        <div style={muted}>{t("model.skill_label")}</div>
        <div style={{ fontSize: 20, fontWeight: 600 }}>
          <SkillText skill={latest.skill} />
        </div>
        <div style={muted}>
          {t("model.skill_bar", { value: fmtRatioPct(th.min_skill) })}
          {margin && (
            <>
              {FILTER_SEPARATOR}
              <span className="model-nowrap">{margin}</span>
            </>
          )}
        </div>
        <OriginSpread spread={latest.origin_skill} />
      </Card>
      <Card data-testid="model-coverage">
        <div style={muted}>{t("model.coverage_label")}</div>
        <div style={{ fontSize: 20, fontWeight: 600 }}>{fmtRatioPct(latest.coverage)}</div>
        <div style={muted}>
          {t("model.coverage_target", { low: fmtRatioPct(th.coverage_low), high: fmtRatioPct(th.coverage_high) })}
        </div>
        {latest.interval_coverage != null && latest.interval_coverage !== latest.coverage && (
          <div style={muted}>{t("model.slot_coverage", { value: fmtRatioPct(latest.interval_coverage) })}</div>
        )}
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
        <SkillText skill={onUsualEstimate(a) ? null : a.skill} />
      </td>
      <td style={cell}>
        <MaeText a={a} />
      </td>
    </tr>
  );
}

function AgencyCard({ a }: { a: MlAgencyEvaluation }) {
  const { t } = useTranslation();
  return (
    <li className="model-card">
      <div className="model-card__head">
        <span className="model-card__title">{a.agency_name ?? a.agency_id}</span>
        {a.forecast_live && <span className="caveat-badge">{t("model.live")}</span>}
      </div>
      {!onUsualEstimate(a) && (
        <div className="model-card__skill">
          <span className="model-card__label">{t("model.col_skill")}</span> <SkillText skill={a.skill} />
        </div>
      )}
      <p className="model-card__meta">
        <span>{t(`model.status_${a.model_status}`)}</span>
        <span>
          <span className="model-card__label">{t("model.col_mae")}</span> <MaeText a={a} />
        </span>
      </p>
    </li>
  );
}

function Agencies({ agencies }: { agencies: MlAgencyEvaluation[] }) {
  const { t } = useTranslation();
  const compact = useMediaQuery(MOBILE_BREAKPOINT_QUERY);
  return (
    <Card>
      <h2 style={{ fontSize: 16, marginTop: 0 }}>{t("model.agencies_title")}</h2>
      <div data-testid="model-agencies">
        {compact ? (
          <ul className="model-cards">
            {agencies.map((a) => (
              <AgencyCard key={a.agency_id} a={a} />
            ))}
          </ul>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={cell}>{t("model.col_agency")}</th>
                <th style={cell}>{t("model.col_status")}</th>
                <th style={cell}>{t("model.col_skill")}</th>
                <th style={cell}>{t("model.col_mae")}</th>
              </tr>
            </thead>
            <tbody>
              {agencies.map((a) => (
                <AgencyRow key={a.agency_id} a={a} />
              ))}
            </tbody>
          </table>
        )}
      </div>
      <p className="model-note" data-testid="model-mae-note">
        {t("model.mae_note")}
      </p>
    </Card>
  );
}

function MetricChanged({ week }: { week: MlEvaluationWeek }) {
  const { t } = useTranslation();
  return <>{t("model.metric_changed", { metric: t(`model.metric_${week.point_metric}`, { defaultValue: week.point_metric }) })}</>;
}

function History({ weeks }: { weeks: MlEvaluationWeek[] }) {
  const { t } = useTranslation();
  const compact = useMediaQuery(MOBILE_BREAKPOINT_QUERY);
  // The week whose scoring metric differs from the week before it.
  const changed = (i: number) => weeks[i + 1] != null && weeks[i + 1].point_metric !== weeks[i].point_metric;
  if (compact) {
    return (
      <ul className="model-cards" data-testid="model-history">
        {weeks.map((w, i) => (
          <li key={w.run_date} className="model-card">
            <div className="model-card__head">
              <span className="model-card__title">{w.run_date}</span>
              <span>{t(w.adopted ? "model.adopted" : "model.not_adopted")}</span>
            </div>
            <div className="model-card__skill">
              <span className="model-card__label">{t("model.col_skill")}</span> <SkillText skill={w.overall_skill} />
            </div>
            {changed(i) && (
              <p className="model-card__meta">
                <MetricChanged week={w} />
              </p>
            )}
          </li>
        ))}
      </ul>
    );
  }
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
        {weeks.map((w, i) => (
          <tr key={w.run_date}>
            <td style={cell}>{w.run_date}</td>
            <td style={cell}>{t(w.adopted ? "model.adopted" : "model.not_adopted")}</td>
            <td style={cell}>
              <SkillText skill={w.overall_skill} />
              {changed(i) && (
                <div style={muted}>
                  <MetricChanged week={w} />
                </div>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ModelPage() {
  const { t } = useTranslation();
  const { data, isPending, error, refetch } = useMlEvaluations();
  return (
    <div className="model-page" style={{ maxWidth: 960, margin: "0 auto", padding: "16px", display: "grid", gap: 16 }}>
      <BackToAgency />
      <PageHeader title={t("model.title")} subtitle={t("model.subtitle")} />
      {isPending && <Skeleton height={200} />}
      {error && <ErrorBanner error={error} onRetry={() => refetch()} />}
      {data && !data.latest && <p style={muted}>{t("model.empty")}</p>}
      {data?.latest && (
        <>
          {!data.forecast_in_use && <p style={muted}>{t("model.flag_off")}</p>}
          <Verdict latest={data.latest} />
          <Headline latest={data.latest} />
          <Agencies agencies={data.latest.agencies} />
          <Card>
            <h2 style={{ fontSize: 16, marginTop: 0 }}>{t("model.history_title")}</h2>
            <History weeks={data.history} />
          </Card>
        </>
      )}
    </div>
  );
}
