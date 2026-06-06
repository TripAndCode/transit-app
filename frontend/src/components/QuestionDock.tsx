import { useState, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { buildCardTemplates, type CardTemplate } from "./askCardTemplates";
import { ParamStrip } from "./ParamStrip";

type QuestionDockProps = {
  agencyId: number;
  busy: boolean;
  /** Called when the user taps 実行. Caller is responsible for thread creation,
   *  appendMsg dispatch, and clearing dock state via the returned promise. */
  onSubmit: (payload: {
    tool: string;
    args: Record<string, unknown>;
    user_summary: string;
  }) => void | Promise<void>;
};

export function QuestionDock({ agencyId, busy, onSubmit }: QuestionDockProps) {
  const { t, i18n } = useTranslation();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const templates = useMemo(() => buildCardTemplates(), [i18n.language]);
  const [composingId, setComposingId] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});

  const composing = templates.find((tpl) => tpl.id === composingId) ?? null;

  function handleChipTap(tpl: CardTemplate) {
    if (busy) return;
    if (composingId === tpl.id) {
      // Same chip from composing → collapse
      setComposingId(null);
      setValues({});
      return;
    }
    // Idle → composing, or swap to a different chip's defaults
    setComposingId(tpl.id);
    setValues(defaultsFor(tpl));
  }

  function handleValueChange(name: string, next: unknown) {
    setValues((prev) => ({ ...prev, [name]: next }));
  }

  function handleRun() {
    if (!composing) return;
    const args = { ...composing.fixed_args, ...values };
    if (typeof args.best_first === "string") {
      args.best_first = args.best_first === "true";
    }
    const summary = composing.buildSummary(values, t);
    onSubmit({ tool: composing.tool, args, user_summary: summary });
    // Caller's onSubmit handler is responsible for awaiting and resetting state.
    setComposingId(null);
    setValues({});
  }

  const missing = composing
    ? composing.params
        .filter((p) => p.kind === "route" && p.required && !values[p.name])
        .map((p) => p.name)
    : [];

  return (
    <div
      style={{
        flexShrink: 0,
        padding: "10px 16px 14px",
        background: "var(--bg-surface, white)",
        borderTop: "1px solid var(--border-soft, rgba(0,0,0,0.06))",
      }}
    >
      <div
        style={{
          border: "1px solid var(--border-soft, rgba(0,0,0,0.08))",
          borderRadius: 12,
          padding: "10px 12px",
          boxShadow: composing ? "0 2px 10px rgba(0,0,0,0.04)" : "none",
          transition: "box-shadow 120ms ease",
        }}
      >
        {composing && (
          <ParamStrip
            template={composing}
            agencyId={agencyId}
            values={values}
            onChange={handleValueChange}
            onSubmit={handleRun}
            busy={busy}
            missing={missing}
          />
        )}

        <div
          role="toolbar"
          aria-label={t("ask.dock.chip_strip_aria")}
          style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}
        >
          {templates.map((tpl) => {
            const active = composingId === tpl.id;
            return (
              <button
                key={tpl.id}
                type="button"
                onClick={() => handleChipTap(tpl)}
                disabled={busy && !active}
                aria-disabled={busy && !active}
                aria-pressed={active}
                style={{
                  background: active ? "var(--accent, #5b6cad)" : "var(--bg-soft, rgba(0,0,0,0.04))",
                  color: active ? "white" : "var(--text-primary, #1a1a1a)",
                  border: "1px solid",
                  borderColor: active
                    ? "var(--accent, #5b6cad)"
                    : "var(--border-soft, rgba(0,0,0,0.08))",
                  borderRadius: 999,
                  padding: "5px 14px",
                  fontSize: 13,
                  cursor: busy && !active ? "not-allowed" : "pointer",
                  opacity: busy && !active ? 0.6 : 1,
                  transition: "background 120ms ease, color 120ms ease",
                }}
                title={t(tpl.title_key)}
              >
                {tpl.emoji} {t(tpl.title_key)}
              </button>
            );
          })}
          {!composing && (
            <span
              style={{
                fontSize: 11,
                color: "var(--text-tertiary, #999)",
                marginLeft: 6,
              }}
            >
              {t("ask.dock.chip_strip_idle_hint")}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function defaultsFor(tpl: CardTemplate): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const p of tpl.params) {
    if (p.kind === "limit") out[p.name] = p.default ?? 5;
    else if (p.kind === "service") out[p.name] = p.default ?? "all";
    else if (p.kind === "granularity") out[p.name] = p.default ?? "week";
    else if (p.kind === "metric") out[p.name] = p.default ?? p.options[0].value;
    // route stays unset (null) — required check will surface it
  }
  return out;
}
