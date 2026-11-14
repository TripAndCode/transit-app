import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { AgencyFeedQualityMonth, FeedQualityMissing } from "../../api/admin";
import { EM_DASH, fixedTenth, formatNumber } from "../../utils/format";

const MUTED = { color: "var(--text-tertiary)" } as const;

type TFunction = ReturnType<typeof useTranslation>["t"];

function pctText(v: number): string {
  return `${fixedTenth(v)}%`;
}

function clampText(v: number): string {
  return `${formatNumber(v, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
}

function secondsText(v: number): string {
  return formatNumber(v, { maximumFractionDigits: 1 });
}

/** Why a month has its grade: the criteria it cannot be judged on and why,
 *  then those it misses, or that it meets them all. */
function gradeReasons(t: TFunction, m: AgencyFeedQualityMonth): string[] {
  const separator = t("admin.agency_diag.quality_list_separator");
  const criterion = (name: string) => t(`admin.agency_diag.quality_criterion.${name}`);
  const reasons: string[] = [];
  if (m.grade_missing.length > 0) {
    const items = m.grade_missing.map((g) =>
      t("admin.agency_diag.quality_grade_missing_item", {
        criterion: criterion(g.criterion),
        reason: t(`admin.agency_diag.quality_missing.${g.reason}`),
      }),
    );
    reasons.push(t("admin.agency_diag.quality_grade_ungraded", { items: items.join(separator) }));
  }
  if (m.grade_failing.length > 0) {
    reasons.push(
      t("admin.agency_diag.quality_grade_failing", { criteria: m.grade_failing.map(criterion).join(separator) }),
    );
  } else if (m.grade != null) {
    reasons.push(t("admin.agency_diag.quality_grade_pass"));
  }
  return reasons;
}

/** A figure, or a dash with the reason the scorecard gives for its absence:
 *  "nothing to measure" must never read like a measured zero. */
function Figure({
  value,
  missing,
  children,
}: {
  value: string | null;
  missing: FeedQualityMissing | null;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  if (value == null) {
    return (
      <span>
        {EM_DASH}{" "}
        <span style={{ ...MUTED, fontSize: "var(--text-xs)" }}>
          {missing ? t(`admin.agency_diag.quality_missing.${missing}`) : null}
        </span>
      </span>
    );
  }
  return (
    <span>
      {value}
      {children != null && (
        <>
          {" "}
          <span style={{ ...MUTED, fontSize: "var(--text-xs)" }}>{children}</span>
        </>
      )}
    </span>
  );
}

/** A trend cell: the figure, or a dash whose title names the reason. */
function Cell({ value, missing }: { value: string | null; missing: FeedQualityMissing | null }) {
  const { t } = useTranslation();
  const reason = value == null && missing ? t(`admin.agency_diag.quality_missing.${missing}`) : undefined;
  return (
    <td title={reason} style={{ textAlign: "right", padding: "3px 0 3px 8px", ...(value == null ? MUTED : {}) }}>
      {value ?? EM_DASH}
    </td>
  );
}

/** The validator's days and each rule it counted, by rule id. A month with
 *  no validated day reads not validated, never "no rule fired". */
function ValidatorFigure({ m }: { m: AgencyFeedQualityMonth }) {
  const { t } = useTranslation();
  const small = { ...MUTED, fontSize: "var(--text-xs)" } as const;
  const failed = m.validator_failed_days ?? 0;
  const rules = Object.entries(m.validator_rules ?? {}).sort(([a], [b]) => a.localeCompare(b));
  return (
    <div style={{ display: "grid", gap: 2 }}>
      <Figure
        value={
          m.validator_ok_days == null || m.validator_rules == null
            ? null
            : t("admin.agency_diag.quality_validator_value", { ok: m.validator_ok_days, days: m.window_days })
        }
        missing={m.validator_ok_days == null ? "not_recorded" : m.validator_missing}
      >
        {t("admin.agency_diag.quality_validator_detail", { polls: m.validator_files_validated ?? 0 })}
      </Figure>
      {failed > 0 && <span style={small}>{t("admin.agency_diag.quality_validator_failed", { failed })}</span>}
      {m.validator_rules != null &&
        (rules.length === 0 ? (
          <span style={small}>{t("admin.agency_diag.quality_validator_none")}</span>
        ) : (
          <ul style={{ ...small, margin: 0, paddingLeft: 16 }}>
            {rules.map(([rule, r]) => (
              <li key={rule}>
                {rule} <span lang="en">{r.title}</span>
                {t("admin.agency_diag.quality_list_separator")}
                {t("admin.agency_diag.quality_validator_rule_counts", {
                  occurrences: r.occurrences,
                  polls: r.files_with_rule,
                  days: r.days,
                })}
              </li>
            ))}
          </ul>
        ))}
      {m.validator_ok_days != null && (
        <span style={small}>{t("admin.agency_diag.quality_validator_note")}</span>
      )}
    </div>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <span style={MUTED}>{label}</span>
      <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{children}</span>
    </>
  );
}

/** The newest month's scorecard in full, then each of the last six months'
 *  headline figures, newest first. `months` arrives newest first. */
export function FeedQualityTrend({ months }: { months: AgencyFeedQualityMonth[] }) {
  const { t } = useTranslation();
  if (months.length === 0) {
    return <div style={{ fontSize: 12.5, ...MUTED }}>{t("admin.agency_diag.quality_empty")}</div>;
  }
  const latest = months[0];
  const fields =
    latest.field_coverage_missing == null
      ? t("admin.agency_diag.quality_fields_value", {
          stop_id: pctText(latest.stop_id_pct ?? 0),
          arrival: pctText(latest.arr_delay_pct ?? 0),
          trip: pctText(latest.schedule_relationship_trip_pct ?? 0),
          stop: pctText(latest.schedule_relationship_stop_pct ?? 0),
        })
      : null;
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div style={{ fontSize: "var(--text-xs)", ...MUTED }}>
        {t("admin.agency_diag.quality_through", {
          month: latest.month.slice(0, 7),
          date: latest.through_date,
          days: latest.window_days,
        })}
      </div>
      <div style={{ display: "grid", gap: 2, fontSize: 12.5 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span style={MUTED}>{t("admin.agency_diag.quality_grade")}</span>
          <span style={{ fontWeight: 600 }}>{latest.grade ?? EM_DASH}</span>
        </div>
        {gradeReasons(t, latest).map((reason) => (
          <div key={reason} style={{ fontSize: "var(--text-xs)", ...MUTED }}>
            {reason}
          </div>
        ))}
        <div style={{ fontSize: "var(--text-xs)", ...MUTED }}>{t("admin.agency_diag.quality_grade_note")}</div>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "104px minmax(0, 1fr)",
          gap: "6px 10px",
          fontSize: 12.5,
          alignItems: "baseline",
        }}
      >
        <Row label={t("admin.agency_diag.quality_rt")}>
          <Figure
            value={latest.rt_completeness_pct == null ? null : pctText(latest.rt_completeness_pct)}
            missing={latest.rt_completeness_missing}
          >
            {t("admin.agency_diag.quality_rt_detail", {
              observed: latest.observed_trips ?? 0,
              planned: latest.planned_trips ?? 0,
            })}
          </Figure>
        </Row>
        <Row label={t("admin.agency_diag.quality_stops")}>
          <Figure
            value={latest.stop_completeness_pct == null ? null : pctText(latest.stop_completeness_pct)}
            missing={latest.stop_completeness_missing}
          >
            {t("admin.agency_diag.quality_stops_detail", {
              observed: latest.observed_stop_events ?? 0,
              planned: latest.planned_stop_events ?? 0,
              days: latest.stop_plan_days,
            })}
          </Figure>
        </Row>
        <Row label={t("admin.agency_diag.quality_age")}>
          <Figure
            value={
              latest.message_age_p50_sec == null || latest.message_age_p95_sec == null
                ? null
                : t("admin.agency_diag.quality_age_value", {
                    p50: secondsText(latest.message_age_p50_sec),
                    p95: secondsText(latest.message_age_p95_sec),
                  })
            }
            missing={latest.message_age_missing}
          >
            {t("admin.agency_diag.quality_age_detail", { polls: latest.message_age_polls })}
          </Figure>
        </Row>
        <Row label={t("admin.agency_diag.quality_stale")}>
          <Figure
            value={latest.message_age_stale_pct == null ? null : clampText(latest.message_age_stale_pct)}
            // A month built before the share was stored has neither the share
            // nor a reason, which the grade reads as not recorded too.
            missing={latest.message_age_missing ?? "not_recorded"}
          >
            {t("admin.agency_diag.quality_stale_detail", {
              stale: latest.message_age_stale_polls ?? 0,
              polls: latest.message_age_polls,
            })}
          </Figure>
        </Row>
        <Row label={t("admin.agency_diag.quality_clamp")}>
          <Figure value={latest.clamp_pct == null ? null : clampText(latest.clamp_pct)} missing={latest.clamp_missing} />
        </Row>
        <Row label={t("admin.agency_diag.quality_fields")}>
          <Figure value={fields} missing={latest.field_coverage_missing} />
        </Row>
        <Row label={t("admin.agency_diag.quality_vp")}>
          <Figure
            value={
              latest.vehicle_positions_available == null
                ? null
                : t(`admin.agency_diag.quality_vp_${latest.vehicle_positions_available ? "yes" : "no"}`)
            }
            missing={latest.vehicle_positions_missing}
          />
        </Row>
        <Row label={t("admin.agency_diag.quality_validator")}>
          <ValidatorFigure m={latest} />
        </Row>
      </div>
      <table
        aria-label={t("admin.agency_diag.quality_trend")}
        style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, fontVariantNumeric: "tabular-nums" }}
      >
        <thead>
          <tr style={MUTED}>
            <th style={{ textAlign: "left", fontWeight: 500 }}>{t("admin.agency_diag.quality_col_month")}</th>
            <th style={{ textAlign: "right", fontWeight: 500 }}>{t("admin.agency_diag.quality_col_rt")}</th>
            <th style={{ textAlign: "right", fontWeight: 500 }}>{t("admin.agency_diag.quality_col_stops")}</th>
            <th style={{ textAlign: "right", fontWeight: 500 }}>{t("admin.agency_diag.quality_col_age")}</th>
            <th style={{ textAlign: "right", fontWeight: 500 }}>{t("admin.agency_diag.quality_col_clamp")}</th>
            <th style={{ textAlign: "right", fontWeight: 500 }}>{t("admin.agency_diag.quality_col_grade")}</th>
          </tr>
        </thead>
        <tbody>
          {months.map((m) => (
            <tr key={m.month} style={{ borderTop: "1px solid var(--border-subtle)" }}>
              <td style={{ padding: "3px 0" }}>{m.month.slice(0, 7)}</td>
              <Cell
                value={m.rt_completeness_pct == null ? null : pctText(m.rt_completeness_pct)}
                missing={m.rt_completeness_missing}
              />
              <Cell
                value={m.stop_completeness_pct == null ? null : pctText(m.stop_completeness_pct)}
                missing={m.stop_completeness_missing}
              />
              <Cell
                value={
                  m.message_age_p50_sec == null
                    ? null
                    : t("admin.agency_diag.quality_seconds", { value: secondsText(m.message_age_p50_sec) })
                }
                missing={m.message_age_missing}
              />
              <Cell value={m.clamp_pct == null ? null : clampText(m.clamp_pct)} missing={m.clamp_missing} />
              <td
                title={gradeReasons(t, m).join(" · ")}
                style={{ textAlign: "right", padding: "3px 0 3px 8px", ...(m.grade == null ? MUTED : {}) }}
              >
                {m.grade ?? EM_DASH}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
