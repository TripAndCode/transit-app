import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { buildCardTemplates, type CardTemplate } from "./askCardTemplates";
import { ParamStrip } from "./ParamStrip";

type QuestionDockProps = {
  agencyId: number;
  busy: boolean;
  /** Called when the user taps 実行. Caller is responsible for thread
   *  creation and appendMsg dispatch; call onRunComplete once that settles
   *  (or immediately, matching the prior fire-and-forget behavior). */
  onSubmit: (payload: {
    tool: string;
    args: Record<string, unknown>;
    user_summary: string;
  }) => void | Promise<void>;
  /** Which template is currently composing (or null if idle). Owned by the
   *  caller so a landing-area pill can open a chip too. */
  composingId: string | null;
  /** Current param values for the composing template. */
  values: Record<string, unknown>;
  /** Chip tapped (toolbar chip, or a landing pill via the caller). */
  onChipTap: (tpl: CardTemplate) => void;
  onValueChange: (name: string, next: unknown) => void;
  /** Called after Run dispatches, so the caller resets composingId/values. */
  onRunComplete: () => void;
  /** Whether to render the persistent chip toolbar. False on the landing
   *  state (no messages yet), where AskLandingCards already exposes every
   *  template — showing the same 5 templates again here duplicated it.
   *  True once a conversation has messages, so a chip toolbar is still
   *  available to start a new kind of question inline. */
  showToolbar: boolean;
};

export function QuestionDock({
  agencyId,
  busy,
  onSubmit,
  composingId,
  values,
  onChipTap,
  onValueChange,
  onRunComplete,
  showToolbar,
}: QuestionDockProps) {
  const { t, i18n } = useTranslation();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const templates = useMemo(() => buildCardTemplates(), [i18n.language]);

  const composing = templates.find((tpl) => tpl.id === composingId) ?? null;

  function handleRun() {
    if (!composing) return;
    const args = { ...composing.fixed_args, ...values };
    if (typeof args.best_first === "string") {
      args.best_first = args.best_first === "true";
    }
    const summary = composing.buildSummary(values, t);
    onSubmit({ tool: composing.tool, args, user_summary: summary });
    onRunComplete();
  }

  const missing = composing
    ? composing.params
        .filter((p) => p.kind === "route" && p.required && !values[p.name])
        .map((p) => p.name)
    : [];

  // Nothing to show: no active template and the toolbar is suppressed
  // (landing state) — an empty bordered box would otherwise sit under the
  // landing cards with nothing in it.
  if (!composing && !showToolbar) return null;

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
            onChange={onValueChange}
            onSubmit={handleRun}
            busy={busy}
            missing={missing}
          />
        )}

        {showToolbar && (
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
                  onClick={() => onChipTap(tpl)}
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
        )}
      </div>
    </div>
  );
}
