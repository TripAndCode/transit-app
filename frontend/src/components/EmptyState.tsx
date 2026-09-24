import type { ReactNode } from "react";
import { Inbox } from "lucide-react";

export type Recovery = { label: string; onClick: () => void };

/** A caller offers at most three at once — more than that stops reading as
 *  "the way out" and starts reading as another list to parse. */
const MAX_RECOVERIES = 3;

type Props = {
  title: string;
  hint?: string;
  /** Defaults to a generic "nothing here" glyph if omitted — pass a more
   *  specific icon when the empty context calls for one. */
  icon?: ReactNode;
  /** Short statements of which filter dimensions excluded everything (e.g.
   *  "路線: A05", "運行種別: 平日") — why the result is empty, not what to do
   *  about it. Rendered as a plain list above any recoveries. */
  reasons?: string[];
  /** Up to three concrete ways out (switch service, clear a route filter,
   *  jump to the agency's latest data date, …), each a verb the caller can
   *  tap immediately. Takes priority over `action` when both are given —
   *  new call sites should pass this instead of `action`. */
  recoveries?: Recovery[];
  /** @deprecated Pass `recoveries` instead. Kept for call sites with a
   *  single obvious fix that haven't migrated yet; ignored when
   *  `recoveries` is also given. */
  action?: { label: string; onClick: () => void };
};

export function EmptyState({ title, hint, icon, reasons, recoveries, action }: Props) {
  const effectiveRecoveries = recoveries?.length ? recoveries.slice(0, MAX_RECOVERIES) : action ? [action] : [];
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "48px 24px",
        color: "var(--text-secondary)",
        textAlign: "center",
      }}
    >
      <div
        style={{
          width: 48,
          height: 48,
          borderRadius: "50%",
          background: "var(--bg-soft)",
          marginBottom: 16,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--text-tertiary)",
        }}
        aria-hidden
      >
        {icon ?? <Inbox size={22} strokeWidth={1.5} />}
      </div>
      <div style={{ fontSize: 16, color: "var(--text-primary)" }}>{title}</div>
      {hint && <div style={{ marginTop: 8 }}>{hint}</div>}
      {reasons && reasons.length > 0 && (
        <ul
          style={{
            marginTop: 10,
            padding: 0,
            listStyle: "none",
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "center",
            gap: 6,
            fontSize: 12,
            color: "var(--text-tertiary)",
          }}
        >
          {reasons.map((reason) => (
            <li
              key={reason}
              style={{
                background: "var(--bg-soft)",
                border: "1px solid var(--border-soft)",
                borderRadius: 20,
                padding: "3px 10px",
              }}
            >
              {reason}
            </li>
          ))}
        </ul>
      )}
      {effectiveRecoveries.length > 0 && (
        <>
          <style>{`
            .empty-state-cta:hover {
              background: var(--accent-soft);
              border-color: var(--accent);
              color: var(--accent);
            }
          `}</style>
          <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: 8, marginTop: 18 }}>
            {effectiveRecoveries.map((recovery) => (
              <button
                key={recovery.label}
                type="button"
                className="empty-state-cta"
                onClick={recovery.onClick}
                style={{
                  padding: "8px 18px",
                  background: "var(--bg-soft)",
                  border: "1px solid var(--border-soft)",
                  borderRadius: 7,
                  fontSize: 13,
                  color: "var(--text-secondary)",
                  cursor: "pointer",
                  transition: "all var(--transition)",
                }}
              >
                {recovery.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
