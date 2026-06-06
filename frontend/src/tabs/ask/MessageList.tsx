import type { TFunction } from "i18next";
import type { ConvMessage, ToolResult } from "../../api/types";
import { RichResult } from "./RichResult";

export function MessageList({
  messages,
  formatRoute,
  t,
}: {
  messages: ConvMessage[];
  formatRoute: (rc: string | null | undefined) => string;
  t: TFunction;
}) {
  return (
    <>
      {messages.map((m) => (
        <Bubble key={m.message_id} msg={m} formatRoute={formatRoute} t={t} />
      ))}
    </>
  );
}

function Bubble({
  msg,
  formatRoute,
  t,
}: {
  msg: ConvMessage;
  formatRoute: (rc: string | null | undefined) => string;
  t: TFunction;
}) {
  const isUser = msg.role === "user";
  const result = msg.result as ToolResult | null;
  const wide = !isUser && (result?.kind === "table" || result?.kind === "series");

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: isUser ? "flex-end" : "flex-start",
        margin: "12px 0",
      }}
    >
      <div
        style={{
          maxWidth: wide ? "100%" : "85%",
          width: wide ? "100%" : undefined,
          padding: "10px 14px",
          background: isUser ? "var(--accent-soft)" : "var(--bg-surface)",
          border: isUser ? "none" : "1px solid var(--border-soft)",
          borderRadius: "var(--radius-lg)",
          whiteSpace: isUser ? "pre-wrap" : undefined,
        }}
      >
        {result ? (
          <RichResult result={result} fallbackText={msg.rendered_summary ?? ""} formatRoute={formatRoute} t={t} />
        ) : (
          <span style={{ whiteSpace: "pre-wrap" }}>{msg.rendered_summary ?? msg.tool}</span>
        )}
        {!isUser && (msg.tool || msg.result) && (
          <details style={{ marginTop: 8, color: "var(--text-tertiary)", fontSize: 12 }}>
            <summary style={{ cursor: "pointer" }}>{t("common.details")}</summary>
            <pre style={{ overflowX: "auto", marginTop: 6, whiteSpace: "pre" }}>
              {JSON.stringify({ tool: msg.tool, args: msg.args, result: msg.result }, null, 2)}
            </pre>
          </details>
        )}
      </div>
    </div>
  );
}
