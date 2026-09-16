import type { TFunction } from "i18next";
import type { ConvMessage, ToolResult } from "../../api/types";
import { RichResult } from "./RichResult";
import "./messageList.css";

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
    <div className={isUser ? "msg-row msg-row--user" : "msg-row msg-row--assistant"}>
      <div
        className={isUser ? "msg-bubble msg-bubble--user" : "msg-bubble msg-bubble--assistant"}
        style={{
          maxWidth: wide ? "100%" : "85%",
          width: wide ? "100%" : undefined,
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
