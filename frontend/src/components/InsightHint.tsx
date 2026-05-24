import { useEffect, useRef, useState } from "react";
import { Info } from "lucide-react";
import { useTranslation } from "react-i18next";

/**
 * Small (?) info icon that opens a quiet popover with a paragraph or two
 * explaining what insights the surrounding chart or tab is for. Click the
 * icon to open; click anywhere else to close. Calm, non-modal.
 */
export function InsightHint({
  title,
  body,
}: {
  title: string;
  body: React.ReactNode;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div ref={ref} style={{ position: "relative", display: "inline-flex" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={t("common.hint_aria")}
        style={{
          background: "transparent",
          border: "none",
          padding: 2,
          cursor: "pointer",
          color: open ? "var(--accent)" : "var(--text-tertiary)",
          display: "inline-flex",
          alignItems: "center",
          transition: "color var(--transition)",
        }}
      >
        <Info size={14} strokeWidth={1.75} />
      </button>
      {open && (
        <div
          role="dialog"
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            left: 0,
            zIndex: 30,
            width: 320,
            background: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius-lg)",
            boxShadow: "0 8px 32px rgba(0,0,0,0.10)",
            padding: "12px 14px",
            color: "var(--text-primary)",
            fontSize: 12,
            lineHeight: 1.6,
          }}
        >
          <div
            style={{
              fontWeight: 600,
              fontSize: 12,
              marginBottom: 6,
              color: "var(--text-primary)",
            }}
          >
            {title}
          </div>
          <div style={{ color: "var(--text-secondary)" }}>{body}</div>
        </div>
      )}
    </div>
  );
}
