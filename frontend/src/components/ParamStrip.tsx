import { useTranslation } from "react-i18next";
import type { CardTemplate, ParamSpec } from "./askCardTemplates";
import { SegmentedPill } from "./paramPills/SegmentedPill";
import { LimitPill } from "./paramPills/LimitPill";
import { RoutePickerPill } from "./paramPills/RoutePickerPill";

export type ParamStripProps = {
  template: CardTemplate;
  agencyId: number;
  values: Record<string, unknown>;
  onChange: (name: string, next: unknown) => void;
  onSubmit: () => void;
  busy: boolean;
  /** Names of required params still missing (drives 実行 disabled and `*` markers). */
  missing: string[];
};

export function ParamStrip({
  template,
  agencyId,
  values,
  onChange,
  onSubmit,
  busy,
  missing,
}: ParamStripProps) {
  const { t } = useTranslation();
  const canRun = !busy && missing.length === 0;

  return (
    <div
      style={{
        display: "flex",
        gap: 8,
        alignItems: "center",
        flexWrap: "wrap",
        padding: "8px 4px 10px",
        borderBottom: "1px dashed var(--border-soft, rgba(0,0,0,0.08))",
        marginBottom: 8,
      }}
    >
      <span style={{ fontWeight: 600, fontSize: 13, color: "var(--text-primary, #1a1a1a)" }}>
        {template.emoji} {t(template.title_key)}
      </span>

      {template.params.map((spec) => {
        const isMissing = missing.includes(spec.name);
        return (
          <span key={spec.name} style={{ display: "inline-flex", alignItems: "center", gap: 2 }}>
            {renderPill(spec, values, onChange, agencyId, busy, t as unknown as (k: string, opts?: object) => string)}
            {isMissing && (
              <span
                aria-label={t("ask.dock.required_marker")}
                style={{ color: "hsl(25, 55%, 50%)", fontSize: 14, fontWeight: 700 }}
              >
                *
              </span>
            )}
          </span>
        );
      })}

      <button
        type="button"
        onClick={onSubmit}
        disabled={!canRun}
        style={{
          marginLeft: "auto",
          background: canRun ? "var(--accent, #4a8aaa)" : "var(--bg-soft, rgba(0,0,0,0.06))",
          color: canRun ? "white" : "var(--text-tertiary, #999)",
          border: "none",
          borderRadius: 999,
          padding: "5px 16px",
          fontSize: 13,
          fontWeight: 600,
          cursor: canRun ? "pointer" : "not-allowed",
          opacity: busy ? 0.7 : 1,
        }}
        aria-busy={busy}
      >
        {busy ? t("ask.dock.running") : t("ask.dock.run")}
      </button>
    </div>
  );
}

function renderPill(
  spec: ParamSpec,
  values: Record<string, unknown>,
  onChange: (name: string, next: unknown) => void,
  agencyId: number,
  busy: boolean,
  t: (k: string, opts?: object) => string,
) {
  const v = values[spec.name];
  if (spec.kind === "limit") {
    return (
      <LimitPill
        label={t("ask.card.param.limit_label")}
        value={typeof v === "number" ? v : (spec.default ?? 5)}
        min={spec.min}
        max={spec.max}
        onChange={(n) => onChange(spec.name, n)}
        disabled={busy}
      />
    );
  }
  if (spec.kind === "service") {
    return (
      <SegmentedPill
        label={t("ask.card.param.service_label")}
        value={(typeof v === "string" ? v : spec.default) ?? "all"}
        options={[
          { value: "all", label: t("ask.card.param.service.all") },
          { value: "weekday", label: t("ask.card.param.service.weekday") },
          { value: "weekend", label: t("ask.card.param.service.weekend") },
        ]}
        onChange={(s) => onChange(spec.name, s)}
        disabled={busy}
      />
    );
  }
  if (spec.kind === "granularity") {
    return (
      <SegmentedPill
        label={t("ask.card.param.granularity_label")}
        value={(typeof v === "string" ? v : spec.default) ?? "week"}
        options={[
          { value: "day", label: t("ask.card.param.granularity.day") },
          { value: "week", label: t("ask.card.param.granularity.week") },
          { value: "month", label: t("ask.card.param.granularity.month") },
        ]}
        onChange={(s) => onChange(spec.name, s)}
        disabled={busy}
      />
    );
  }
  if (spec.kind === "metric") {
    // Used only by ontime_rank for best/worst direction.
    const value = (typeof v === "string" ? v : spec.default) ?? spec.options[0].value;
    return (
      <SegmentedPill
        label={t("ask.card.param.metric_label", { defaultValue: "指標" })}
        value={value}
        options={spec.options.map((o) => ({ value: o.value, label: t(o.label_key) }))}
        onChange={(s) => onChange(spec.name, s)}
        disabled={busy}
      />
    );
  }
  if (spec.kind === "route") {
    return (
      <RoutePickerPill
        label={t("ask.card.param.route_label")}
        value={(typeof v === "string" && v) ? v : null}
        agencyId={agencyId}
        placeholder={t("ask.card.param.route_placeholder")}
        onChange={(rc) => onChange(spec.name, rc)}
        disabled={busy}
      />
    );
  }
  // Exhaustiveness guard. ParamSpec is a discriminated union — adding a new
  // kind upstream without updating this switch should fail at type-check.
  void (spec as never);
  return null;
}
