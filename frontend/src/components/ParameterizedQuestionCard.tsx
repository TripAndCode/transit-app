import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { FilterCtx } from "../api/types";
import { useRoutes } from "../api/hooks";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type ParamSpec =
  | { kind: "route"; name: string; required?: boolean }
  | {
      kind: "metric";
      name: string;
      options: Array<{ value: string; label_key: string }>;
      default?: string;
    }
  | { kind: "limit"; name: string; min?: number; max?: number; default?: number }
  | { kind: "granularity"; name: string; default?: "day" | "week" | "month" }
  | { kind: "service"; name: string; default?: "all" | "weekday" | "weekend" };

export type CardTemplate = {
  /** Unique slug, used in user_summary fallback. */
  id: string;
  /** i18n key under `ask.card.<id>.title` */
  title_key: string;
  /** Emoji shown next to title. */
  emoji: string;
  /** Tool slug to dispatch (e.g. "top_n", "trend"). */
  tool: string;
  /** Static args merged into the final args (e.g. {"metric": "avg_delay"} when not user-selectable). */
  fixed_args?: Record<string, unknown>;
  /** Parameter inputs to render. */
  params: ParamSpec[];
  /** Renders the user_summary preview string from current values. */
  buildSummary: (
    values: Record<string, unknown>,
    t: (key: string, opts?: object) => string,
  ) => string;
};

export type ParameterizedQuestionCardProps = {
  template: CardTemplate;
  agencyId: number;
  filterCtx: FilterCtx;
  /** Disabled while parent is dispatching (prevents double-submit). */
  busy?: boolean;
  onSubmit: (payload: {
    tool: string;
    args: Record<string, unknown>;
    user_summary: string;
  }) => void;
};

// ---------------------------------------------------------------------------
// Private input renderers
// ---------------------------------------------------------------------------

type RouteSelectProps = {
  agencyId: number;
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
};

function RouteSelect({ agencyId, value, onChange, placeholder }: RouteSelectProps) {
  const { data, isPending } = useRoutes(agencyId);

  const options = useMemo(() => {
    if (!data) return [];
    const seen = new Set<string>();
    const result: { code: string; label: string }[] = [];
    for (const r of data) {
      if (!r.route_code || seen.has(r.route_code)) continue;
      seen.add(r.route_code);
      const name = r.route_short_name || r.route_long_name || r.route_id || r.route_code;
      const long = r.route_long_name?.trim();
      const label = long && long !== name ? `${name} ${long}` : name;
      result.push({ code: r.route_code, label });
    }
    return result.sort((a, b) => a.label.localeCompare(b.label));
  }, [data]);

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={isPending}
      style={selectStyle}
    >
      <option value="">{isPending ? "…" : placeholder}</option>
      {options.map((o) => (
        <option key={o.code} value={o.code}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

const selectStyle: React.CSSProperties = {
  fontSize: 13,
  padding: "4px 8px",
  borderRadius: 6,
  border: "1px solid var(--border-subtle, rgba(0,0,0,0.12))",
  background: "var(--bg-input, white)",
  color: "var(--text-primary, #1a1a1a)",
  minWidth: 120,
  cursor: "pointer",
};

const segmentContainerStyle: React.CSSProperties = {
  display: "inline-flex",
  borderRadius: 6,
  overflow: "hidden",
  border: "1px solid var(--border-subtle, rgba(0,0,0,0.12))",
};

function segmentButtonStyle(pressed: boolean): React.CSSProperties {
  return {
    fontSize: 12,
    padding: "4px 10px",
    border: "none",
    borderRight: "1px solid var(--border-subtle, rgba(0,0,0,0.12))",
    background: pressed ? "var(--accent, #4a8aaa)" : "var(--bg-card, white)",
    color: pressed ? "white" : "var(--text-secondary, #555)",
    cursor: "pointer",
    fontWeight: pressed ? 600 : 400,
    lineHeight: 1.4,
  };
}

// ---------------------------------------------------------------------------
// Shared label+asterisk component
// ---------------------------------------------------------------------------

type LabelProps = {
  text: string;
  required?: boolean;
  missing?: boolean;
};

function ParamLabel({ text, required, missing }: LabelProps) {
  return (
    <div
      style={{
        fontSize: 11,
        color: "var(--text-secondary, #666)",
        marginBottom: 3,
        fontWeight: 500,
      }}
    >
      {text}
      {required && missing && (
        <span
          aria-label="required"
          style={{ color: "hsl(25,55%,50%)", marginLeft: 2, fontWeight: 700 }}
        >
          *
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ParameterizedQuestionCard({
  template,
  agencyId,
  filterCtx: _filterCtx,
  busy,
  onSubmit,
}: ParameterizedQuestionCardProps): JSX.Element {
  const { t, i18n } = useTranslation();

  // Initialise state from param defaults
  const initValues = useMemo(() => {
    const v: Record<string, unknown> = {};
    for (const p of template.params) {
      if (p.kind === "route") {
        v[p.name] = "";
      } else if (p.kind === "metric") {
        v[p.name] = p.default ?? p.options[0]?.value ?? "";
      } else if (p.kind === "limit") {
        v[p.name] = p.default ?? 5;
      } else if (p.kind === "granularity") {
        v[p.name] = p.default ?? "day";
      } else if (p.kind === "service") {
        v[p.name] = p.default ?? "all";
      }
    }
    return v;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [template.id]);

  const [values, setValues] = useState<Record<string, unknown>>(initValues);

  function setValue(name: string, val: unknown) {
    setValues((prev) => ({ ...prev, [name]: val }));
  }

  // Validation
  const validation = useMemo(() => {
    const missing: string[] = [];
    for (const p of template.params) {
      if (p.kind === "route" && p.required !== false) {
        // route is required by default unless explicitly required=false
        if (!values[p.name]) missing.push(p.name);
      }
    }
    return { valid: missing.length === 0, missing };
  }, [values, template]);

  // i18next's TFunction has a stricter overloaded signature than the simple
  // (key: string, opts?: object) => string we expose on CardTemplate.buildSummary.
  // Cast to the loose form so consumers don't need to depend on i18next types.
  const tLoose = t as (key: string, opts?: object) => string;

  // Live summary — re-computes on locale change as well
  const summary = useMemo(
    () => template.buildSummary(values, tLoose),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [values, template, i18n.language],
  );

  function handleSubmit() {
    if (!validation.valid || busy) return;
    const args = { ...template.fixed_args, ...values };
    const user_summary = template.buildSummary(values, tLoose);
    onSubmit({ tool: template.tool, args, user_summary });
  }

  const canSubmit = validation.valid && !busy;

  return (
    <div
      style={{
        padding: "var(--space-4, 16px)",
        border: "1px solid var(--border-subtle, rgba(0,0,0,0.08))",
        borderRadius: 10,
        background: "var(--bg-card, white)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-3, 10px)",
        transition: "box-shadow 0.15s ease",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLDivElement).style.boxShadow =
          "0 1px 6px rgba(0,0,0,0.05)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLDivElement).style.boxShadow = "none";
      }}
    >
      {/* Header */}
      <div
        style={{
          fontSize: 14,
          fontWeight: 600,
          color: "var(--text-primary, #1a1a1a)",
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <span aria-hidden="true">{template.emoji}</span>
        <span>{t(template.title_key)}</span>
      </div>

      {/* Params row */}
      {template.params.length > 0 && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--space-2, 8px)",
            alignItems: "flex-end",
          }}
        >
          {template.params.map((p) => {
            const isMissing = validation.missing.includes(p.name);

            if (p.kind === "route") {
              return (
                <div key={p.name}>
                  <ParamLabel
                    text={t("ask.card.param.route_label")}
                    required
                    missing={isMissing}
                  />
                  <RouteSelect
                    agencyId={agencyId}
                    value={(values[p.name] as string) ?? ""}
                    onChange={(v) => setValue(p.name, v)}
                    placeholder={t("ask.card.param.route_placeholder")}
                  />
                </div>
              );
            }

            if (p.kind === "metric") {
              return (
                <div key={p.name}>
                  <ParamLabel text={t("ask.card.param.metric_label")} />
                  <select
                    value={(values[p.name] as string) ?? ""}
                    onChange={(e) => setValue(p.name, e.target.value)}
                    style={selectStyle}
                  >
                    {p.options.map((o) => (
                      <option key={o.value} value={o.value}>
                        {t(o.label_key)}
                      </option>
                    ))}
                  </select>
                </div>
              );
            }

            if (p.kind === "limit") {
              return (
                <div key={p.name}>
                  <ParamLabel text={t("ask.card.param.limit_label")} />
                  <input
                    type="number"
                    value={(values[p.name] as number) ?? (p.default ?? 5)}
                    min={p.min ?? 1}
                    max={p.max ?? 20}
                    onChange={(e) =>
                      setValue(p.name, Math.max(p.min ?? 1, Math.min(p.max ?? 20, Number(e.target.value))))
                    }
                    style={{
                      ...selectStyle,
                      width: 60,
                      textAlign: "center",
                    }}
                  />
                </div>
              );
            }

            if (p.kind === "granularity") {
              const current = (values[p.name] as string) ?? (p.default ?? "day");
              const opts: Array<{ value: "day" | "week" | "month"; labelKey: string }> = [
                { value: "day", labelKey: "ask.card.param.granularity.day" },
                { value: "week", labelKey: "ask.card.param.granularity.week" },
                { value: "month", labelKey: "ask.card.param.granularity.month" },
              ];
              return (
                <div key={p.name}>
                  <ParamLabel text={t("ask.card.param.granularity_label")} />
                  <div style={segmentContainerStyle} role="group">
                    {opts.map((o, idx) => (
                      <button
                        key={o.value}
                        type="button"
                        aria-pressed={current === o.value}
                        onClick={() => setValue(p.name, o.value)}
                        style={{
                          ...segmentButtonStyle(current === o.value),
                          // Remove right border from last item
                          borderRight:
                            idx < opts.length - 1
                              ? "1px solid var(--border-subtle, rgba(0,0,0,0.12))"
                              : "none",
                        }}
                      >
                        {t(o.labelKey)}
                      </button>
                    ))}
                  </div>
                </div>
              );
            }

            if (p.kind === "service") {
              const current = (values[p.name] as string) ?? (p.default ?? "all");
              const opts: Array<{ value: "all" | "weekday" | "weekend"; labelKey: string }> = [
                { value: "all", labelKey: "ask.card.service.all" },
                { value: "weekday", labelKey: "ask.card.service.weekday" },
                { value: "weekend", labelKey: "ask.card.service.weekend" },
              ];
              return (
                <div key={p.name}>
                  <ParamLabel text={t("ask.card.param.service_label")} />
                  <div style={segmentContainerStyle} role="group">
                    {opts.map((o, idx) => (
                      <button
                        key={o.value}
                        type="button"
                        aria-pressed={current === o.value}
                        onClick={() => setValue(p.name, o.value)}
                        style={{
                          ...segmentButtonStyle(current === o.value),
                          borderRight:
                            idx < opts.length - 1
                              ? "1px solid var(--border-subtle, rgba(0,0,0,0.12))"
                              : "none",
                        }}
                      >
                        {t(o.labelKey)}
                      </button>
                    ))}
                  </div>
                </div>
              );
            }

            return null;
          })}
        </div>
      )}

      {/* Summary preview */}
      <div
        style={{
          fontSize: 12,
          fontStyle: "italic",
          color: "var(--text-secondary, #666)",
          padding: "8px 12px",
          background: "var(--bg-soft, rgba(0,0,0,0.03))",
          borderRadius: 6,
          lineHeight: 1.5,
        }}
      >
        📝 "{summary}"
      </div>

      {/* Submit button — right-aligned */}
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={!canSubmit}
          style={{
            background: canSubmit
              ? "var(--accent, #4a8aaa)"
              : "var(--border-subtle, rgba(0,0,0,0.12))",
            color: canSubmit ? "white" : "var(--text-tertiary, #aaa)",
            border: "none",
            borderRadius: 999,
            padding: "6px 18px",
            fontSize: 13,
            fontWeight: 600,
            cursor: canSubmit ? "pointer" : "not-allowed",
            transition: "background 0.15s ease, opacity 0.15s ease",
            opacity: busy ? 0.6 : 1,
          }}
        >
          {t("ask.card.execute")}
        </button>
      </div>
    </div>
  );
}
