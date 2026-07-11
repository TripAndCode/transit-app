import type { ReactNode } from "react";
import { Inbox } from "lucide-react";

type Props = {
  title: string;
  hint?: string;
  /** Defaults to a generic "nothing here" glyph if omitted — pass a more
   *  specific icon when the empty context calls for one. */
  icon?: ReactNode;
  /** Optional single recovery action (e.g. "reset the filter that's
   *  causing the empty result"). Most EmptyState call sites have no single
   *  obvious fix and should omit this. */
  action?: { label: string; onClick: () => void };
};

export function EmptyState({ title, hint, icon, action }: Props) {
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
      {action && (
        <>
          <style>{`
            .empty-state-cta:hover {
              background: var(--accent-soft);
              border-color: var(--accent);
              color: var(--accent);
            }
          `}</style>
          <button
            type="button"
            className="empty-state-cta"
            onClick={action.onClick}
            style={{
              marginTop: 18,
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
            {action.label}
          </button>
        </>
      )}
    </div>
  );
}
