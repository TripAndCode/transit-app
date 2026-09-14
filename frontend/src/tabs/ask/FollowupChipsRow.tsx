import { useRef, useState } from "react";
import type { TFunction } from "i18next";
import type { ConvMessage } from "../../api/types";
import { FOLLOWUP_CHIPS } from "../../components/askFollowupChips";
import { ErrorBanner } from "../../components/ErrorBanner";
import { stopEvidence, type StopFocus } from "./stopEvidence";

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

  // Tracks the chip most recently clicked *for the currently-grounded
  // message* so it can be visually de-emphasized instead of re-showing every
  // chip with identical weight after it was just asked. Keyed on both the
  // chip id and the grounding message id (not just the chip id) so a new
  // tool result -- a fresh context to ask the same question type about --
  // clears the de-emphasis automatically, with no reset effect needed.
  const [lastClickedChip, setLastClickedChip] = useState<{ msgId: number; chipId: string } | null>(null);

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

  // The <input maxLength> below already caps draftValue at maxChars, so
  // trimmed can never exceed it -- only the lower bound needs checking here.
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
      {!compact && !focus && (
      <div
        role="group"
        aria-label={t("ask.followup_chips.panel_aria")}
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
        }}
      >
        {FOLLOWUP_CHIPS.map((chip) => {
          const isLastClicked =
            lastClickedChip?.msgId === lastResultMsgId && lastClickedChip?.chipId === chip.id;
          return (
            <button
              key={chip.id}
              type="button"
              aria-pressed={isLastClicked}
              onClick={() => {
                setLastClickedChip({ msgId: lastResultMsgId, chipId: chip.id });
                onFollowup(lastResultMsgId, t(chip.prompt_key), false);
              }}
              style={{
                padding: "5px 12px",
                fontSize: 12,
                background: "var(--bg-soft, #f4f4f5)",
                color: "var(--text-secondary, #52525b)",
                border: "1px solid var(--border-soft, #e4e4e7)",
                borderRadius: 999,
                cursor: "pointer",
                whiteSpace: "nowrap",
                transition: "background 0.15s, opacity 0.15s",
                // De-emphasize (not remove) the chip just asked about this
                // result -- it's still available to ask again, but shouldn't
                // read with the same weight as the untried options next to it.
                opacity: isLastClicked ? 0.55 : 1,
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLButtonElement).style.background = "var(--bg-soft-hover)";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLButtonElement).style.background = "var(--bg-soft)";
              }}
            >
              {t(chip.label_key)}
            </button>
          );
        })}
      </div>
      )}

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
