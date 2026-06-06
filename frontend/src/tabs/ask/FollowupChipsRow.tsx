import type { TFunction } from "i18next";
import type { ConvMessage } from "../../api/types";
import { FOLLOWUP_CHIPS } from "../../components/askFollowupChips";

/** Bottom-of-thread follow-up chips. Grounds every follow-up on the most
 *  recent assistant message that carries a tool result, so multi-turn
 *  follow-ups never compound LLM-generated answers. Hidden when the thread
 *  has no tool result to ground on. */
export function FollowupChipsRow({
  messages,
  t,
  onFollowup,
}: {
  messages: ConvMessage[];
  t: TFunction;
  onFollowup: (contextMsgId: number, question: string) => void;
}) {
  const lastResultMsgId = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === "assistant" && m.tool && m.result) return m.message_id;
    }
    return null;
  })();
  if (lastResultMsgId == null) return null;

  return (
    <div
      role="group"
      aria-label={t("ask.followup_chips.panel_aria")}
      style={{
        marginTop: 8,
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
  );
}
