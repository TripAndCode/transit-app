import type { TFunction } from "i18next";
import type { ConvMessage } from "../../api/types";
import { FOLLOWUP_CHIPS } from "../../components/askFollowupChips";
import { ErrorBanner } from "../../components/ErrorBanner";

// Fallback used only for the brief window before /ask/followup-enabled
// resolves; the server-supplied `maxChars` (pipeline/query/followup.py's
// MAX_QUESTION_CHARS) is authoritative and always wins once loaded.
const FOLLOWUP_MAX_CHARS_FALLBACK = 500;

/** Bottom-of-thread follow-up chips plus a free-text box. Grounds every
 *  follow-up (chip or typed) on the most recent assistant message that
 *  carries a tool result, so multi-turn follow-ups never compound
 *  LLM-generated answers. Hidden when the thread has no tool result to
 *  ground on. */
export function FollowupChipsRow({
  messages,
  t,
  onFollowup,
  draftValue,
  onDraftChange,
  error,
  maxChars = FOLLOWUP_MAX_CHARS_FALLBACK,
}: {
  messages: ConvMessage[];
  t: TFunction;
  onFollowup: (contextMsgId: number, question: string) => void;
  draftValue: string;
  onDraftChange: (next: string) => void;
  error?: unknown;
  maxChars?: number;
}) {
  const lastResultMsgId = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === "assistant" && m.tool && m.result) return m.message_id;
    }
    return null;
  })();
  if (lastResultMsgId == null) return null;

  // The <input maxLength> below already caps draftValue at maxChars, so
  // trimmed can never exceed it -- only the lower bound needs checking here.
  const trimmed = draftValue.trim();
  const canSubmit = trimmed.length > 0;

  function submitDraft() {
    // lastResultMsgId is non-null here (the early return above guarantees
    // it), but TS doesn't retain that narrowing across this nested function
    // boundary, so the null check stays for type safety, not defensively.
    if (!canSubmit || lastResultMsgId == null) return;
    onFollowup(lastResultMsgId, trimmed);
  }

  return (
    <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 8 }}>
      <div
        role="group"
        aria-label={t("ask.followup_chips.panel_aria")}
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
        }}
      >
        {FOLLOWUP_CHIPS.map((chip) => (
          <button
            key={chip.id}
            type="button"
            onClick={() => onFollowup(lastResultMsgId, t(chip.prompt_key))}
            style={{
              padding: "5px 12px",
              fontSize: 12,
              background: "var(--bg-soft, #f4f4f5)",
              color: "var(--text-secondary, #52525b)",
              border: "1px solid var(--border-soft, #e4e4e7)",
              borderRadius: 999,
              cursor: "pointer",
              whiteSpace: "nowrap",
              transition: "background 0.15s",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = "var(--bg-soft-hover, #e4e4e7)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = "var(--bg-soft, #f4f4f5)";
            }}
          >
            {t(chip.label_key)}
          </button>
        ))}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          submitDraft();
        }}
        style={{ display: "flex", gap: 6 }}
      >
        <input
          type="text"
          value={draftValue}
          onChange={(e) => onDraftChange(e.target.value)}
          placeholder={t("ask.followup_placeholder")}
          maxLength={maxChars}
          aria-label={t("ask.followup_placeholder")}
          style={{
            flex: 1,
            padding: "7px 12px",
            fontSize: 13,
            border: "1px solid var(--border-soft, #e4e4e7)",
            borderRadius: 8,
            background: "var(--bg-surface, white)",
            color: "var(--text-primary, #1a1a1a)",
          }}
        />
        <button
          type="submit"
          disabled={!canSubmit}
          style={{
            padding: "7px 14px",
            fontSize: 13,
            background: canSubmit ? "var(--accent, #5b6cad)" : "var(--bg-soft, rgba(0,0,0,0.06))",
            color: canSubmit ? "white" : "var(--text-tertiary, #999)",
            border: "1px solid",
            borderColor: canSubmit ? "var(--accent, #5b6cad)" : "var(--border-soft, rgba(0,0,0,0.08))",
            borderRadius: 8,
            cursor: canSubmit ? "pointer" : "not-allowed",
            whiteSpace: "nowrap",
          }}
        >
          {t("ask.followup_send")}
        </button>
      </form>

      {error != null && <ErrorBanner error={error} />}
    </div>
  );
}
