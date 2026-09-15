import { useRef } from "react";
import type { TFunction } from "i18next";
import type { ConvMessage } from "../../api/types";
import { ErrorBanner } from "../../components/ErrorBanner";
import { stopEvidence, type StopFocus } from "./stopEvidence";

// Fallback used only for the brief window before /ask/followup-enabled
// resolves; the server-supplied `maxChars` (pipeline/query/followup.py's
// MAX_QUESTION_CHARS) is authoritative and always wins once loaded.
const FOLLOWUP_MAX_CHARS_FALLBACK = 500;

/** Bottom-of-thread follow-up composer. Grounds a typed follow-up on an
 *  explicit `focus` selection when one is given, otherwise on the most
 *  recent assistant message that carries a tool result, so multi-turn
 *  follow-ups never compound LLM-generated answers. Hidden when there is
 *  no message to ground on (no tool result, or a stale/unavailable focus). */
export function FollowupChipsRow({
  messages,
  t,
  onFollowup,
  draftValue,
  onDraftChange,
  error,
  maxChars = FOLLOWUP_MAX_CHARS_FALLBACK,
  focus = null,
  compact = false,
}: {
  messages: ConvMessage[];
  t: TFunction;
  /** `isDraft` is an explicit source flag, not inferred from text -- a typed
   *  draft that happens to exactly match a canned chip's translated prompt
   *  must not be misattributed to the chip (or vice versa). */
  onFollowup: (contextMsgId: number, question: string, isDraft: boolean, rowIndex?: number) => void;
  draftValue: string;
  onDraftChange: (next: string) => void;
  error?: unknown;
  maxChars?: number;
  focus?: StopFocus | null;
  compact?: boolean;
}) {
  // Guards the native Enter-to-submit against IME composition: on some
  // browser/OS combos (notably Safari + macOS), pressing Enter to confirm a
  // kana→kanji conversion candidate can also fire the form's submit before
  // the user finished typing. Declared before the early return below so the
  // hook order stays unconditional.
  const isComposingRef = useRef(false);

  const lastResultMsgId = (() => {
    if (focus) {
      const source = messages.find((message) => message.message_id === focus.messageId);
      if (source && stopEvidence(source)?.some((point) => point.sequence === focus.sequence && point.name === focus.name &&
        point.patternId === focus.patternId && point.rowIndex === focus.rowIndex && point.stopId === focus.stopId)) return focus.messageId;
      return null;
    }
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === "assistant" && m.tool && m.result) return m.message_id;
    }
    return null;
  })();
  if (lastResultMsgId == null) return null;
  if (!focus && messages.find((message) => message.message_id === lastResultMsgId)?.tool === "route_stop_patterns") {
    return <p className="investigation-caption">{t("ask.evidence.select_to_ask")}</p>;
  }

  // The <input maxLength> below caps typed input at availableChars, but a
  // focus toggling on after text was already entered can shrink
  // availableChars below the current draft length, so canSubmit still
  // re-checks the combined length against the server's maxChars directly.
  const trimmed = draftValue.trim();
  const prefix = focus ? t(focus.patternId ? "ask.evidence.pattern_focus" : "ask.evidence.focus_context", { sequence: focus.sequence, name: focus.name, stopId: focus.stopId, patternId: focus.patternId }) + "\n" : "";
  const availableChars = Math.max(0, maxChars - prefix.length);
  const canSubmit = trimmed.length > 0 && prefix.length + trimmed.length <= maxChars;

  function submitDraft() {
    // lastResultMsgId is non-null here (the early return above guarantees
    // it), but TS doesn't retain that narrowing across this nested function
    // boundary, so the null check stays for type safety, not defensively.
    if (!canSubmit || lastResultMsgId == null) return;
    if (focus?.rowIndex !== undefined) onFollowup(lastResultMsgId, `${trimmed}\n${prefix.trimEnd()}`, true, focus.rowIndex);
    else onFollowup(lastResultMsgId, focus ? `${trimmed}\n${prefix.trimEnd()}` : trimmed, true);
  }

  return (
    <div className={compact ? "ask-context-composer" : undefined} style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 8 }}>
      {compact && <p className="investigation-caption">{t("ask.evidence.ask_hint")}</p>}
      {focus && <div className="ask-selection-context">{prefix.trimEnd()}</div>}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (isComposingRef.current) return;
          submitDraft();
        }}
        style={{ display: "flex", gap: 6 }}
      >
        <input
          type="text"
          value={draftValue}
          onChange={(e) => onDraftChange(e.target.value)}
          onCompositionStart={() => {
            isComposingRef.current = true;
          }}
          onCompositionEnd={() => {
            isComposingRef.current = false;
          }}
          placeholder={t("ask.followup_placeholder")}
          maxLength={availableChars}
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
      {prefix.length + trimmed.length > maxChars && <p role="status">{t("ask.evidence.too_long")}</p>}
      {compact && <p className="investigation-caption">{t("ask.evidence.ai_notice")}</p>}

      {error != null && <ErrorBanner error={error} />}
    </div>
  );
}
