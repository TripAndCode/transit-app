import { useState } from "react";
import { useTranslation } from "react-i18next";
import { PREVIEW_ASK_EXCHANGES } from "./previewData";

// `literalText` carries the user's own typed words verbatim (not run through
// `t()` -- it isn't translation content, it's user input); every canned chip
// question/reply instead carries a `textKey` into the locale files.
type Message = { role: "user" | "assistant" } & ({ textKey: string } | { literalText: string });

/** Dashboard-preview Ask tab: suggestion chips and a free-text input that
 *  both genuinely drive a (locally-mocked) conversation -- clicking a chip
 *  or submitting typed text appends a real message to the thread below.
 *  Real Ask routing (rules -> embedding nearest-neighbour -> RAG LLM) needs
 *  a signed-in session and a real agency; this exists purely so a
 *  prospective user can see the shape of that conversation before either
 *  exists, not to reimplement any part of the real pipeline. */
export function PreviewAskPanel() {
  const { t } = useTranslation();
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");

  function askChip(index: number) {
    const exchange = PREVIEW_ASK_EXCHANGES[index];
    setMessages((prev) => [...prev, { role: "user", textKey: exchange.questionKey }, { role: "assistant", textKey: exchange.answerKey }]);
  }

  function submitDraft() {
    const text = draft.trim();
    if (!text) return;
    setMessages((prev) => [
      ...prev,
      { role: "user", literalText: text },
      { role: "assistant", textKey: "landing.preview.ask.generic_reply" },
    ]);
    setDraft("");
  }

  return (
    <div style={{ padding: 20, display: "flex", flexDirection: "column", height: "100%", boxSizing: "border-box" }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 7, marginBottom: 14 }}>
        {PREVIEW_ASK_EXCHANGES.map((exchange, i) => (
          <button
            key={exchange.questionKey}
            type="button"
            onClick={() => askChip(i)}
            style={{
              padding: "6px 12px",
              background: "var(--bg-soft)",
              border: "1px solid var(--border-soft)",
              borderRadius: 20,
              fontSize: 12,
              color: "var(--text-secondary)",
              cursor: "pointer",
            }}
          >
            {t(exchange.questionKey)}
          </button>
        ))}
      </div>

      <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 8, marginBottom: 14 }}>
        {messages.length === 0 && (
          <div style={{ color: "var(--text-tertiary)", fontSize: 13 }}>{t("landing.preview.ask.empty_hint")}</div>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            style={{
              alignSelf: m.role === "user" ? "flex-end" : "flex-start",
              maxWidth: "80%",
              background: m.role === "user" ? "var(--accent-soft)" : "var(--bg-surface)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "var(--radius-lg)",
              padding: "8px 12px",
              fontSize: 13,
              color: "var(--text-primary)",
            }}
          >
            {"literalText" in m ? m.literalText : t(m.textKey)}
          </div>
        ))}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          submitDraft();
        }}
        style={{ display: "flex", gap: 8 }}
      >
        <label htmlFor="landing-preview-ask-input" style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>
          {t("landing.preview.ask.input_label")}
        </label>
        <input
          id="landing-preview-ask-input"
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={t("landing.preview.ask.input_placeholder")}
          style={{
            flex: 1,
            padding: "8px 12px",
            borderRadius: "var(--radius)",
            border: "1px solid var(--border-soft)",
            fontSize: 13,
          }}
        />
        <button
          type="submit"
          style={{
            padding: "8px 16px",
            borderRadius: "var(--radius)",
            border: "none",
            background: "var(--accent)",
            color: "#fff",
            fontWeight: 600,
            fontSize: 13,
            cursor: "pointer",
          }}
        >
          {t("landing.preview.ask.send")}
        </button>
      </form>
    </div>
  );
}
