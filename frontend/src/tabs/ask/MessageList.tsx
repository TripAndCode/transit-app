import type { TFunction } from "i18next";
import type { ConvMessage, ToolResult } from "../../api/types";
import { RichResult } from "./RichResult";
import type { NextStepAction } from "./nextStepChips";
import "./messageList.css";

export function MessageList({
  messages,
  formatRoute,
  t,
  onChip,
}: {
  messages: ConvMessage[];
  formatRoute: (rc: string | null | undefined) => string;
  t: TFunction;
  /** Forwarded to each message's evidence card next-step chips (see
   *  RichResult/nextStepChips). Omitted in read-only/historical contexts
   *  that don't wire up chip actions (e.g. the full-log disclosure). */
  onChip?: (action: NextStepAction) => void;
}) {
  return (
    <>
      {messages.map((m) => (
        <Bubble key={m.message_id} msg={m} formatRoute={formatRoute} t={t} onChip={onChip} />
      ))}
    </>
  );
}

function Bubble({
  msg,
  formatRoute,
  t,
  onChip,
}: {
  msg: ConvMessage;
  formatRoute: (rc: string | null | undefined) => string;
  t: TFunction;
  onChip?: (action: NextStepAction) => void;
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
          <RichResult
            result={result}
            fallbackText={msg.rendered_summary ?? ""}
            formatRoute={formatRoute}
            t={t}
            tool={msg.tool}
            args={msg.args}
            conditions={msg.conditions}
            onChip={onChip}
          />
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
