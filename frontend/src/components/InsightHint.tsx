import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Info } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useTopmostEscape } from "../hooks/useFocusTrap";
import { Z_INDEX } from "../styles/zIndex";
import { usePortalPlacement } from "./usePortalPlacement";

/**
 * Small (?) info icon that opens a quiet popover with a paragraph or two
 * explaining what insights the surrounding chart or tab is for. Click the
 * icon to open; click anywhere else, or press Escape, to close. Calm,
 * non-modal.
 *
 * The popover renders into <body> and is placed against the viewport, so a
 * neighbouring column or a clipping ancestor never cuts it off.
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
  const titleId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  useTopmostEscape(open, () => {
    setOpen(false);
    triggerRef.current?.focus();
  });

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target) || popoverRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  usePortalPlacement(open, () => triggerRef.current?.getBoundingClientRect(), popoverRef, "bottom");

  return (
    <div style={{ display: "inline-flex" }}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={t("common.hint_aria")}
        aria-expanded={open}
        style={{
          background: "transparent",
          border: "none",
          padding: 2,
          // WCAG 2.5.8's 24 px minimum target, around a 16 px glyph.
          minWidth: 24,
          minHeight: 24,
          justifyContent: "center",
          cursor: "pointer",
          color: open ? "var(--accent)" : "var(--text-tertiary)",
          display: "inline-flex",
          alignItems: "center",
          transition: "color var(--transition)",
        }}
      >
        <Info size={16} strokeWidth={1.75} />
      </button>
      {open &&
        createPortal(
          <div
            ref={popoverRef}
            role="dialog"
            aria-labelledby={titleId}
            style={{
              position: "fixed",
              left: 0,
              top: 0,
              zIndex: Z_INDEX.popover,
              width: 320,
              maxWidth: "calc(100vw - 24px)",
              background: "var(--bg-surface)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "var(--radius-lg)",
              boxShadow: "var(--el-2)",
              padding: "12px 14px",
              color: "var(--text-primary)",
              fontSize: 12,
              lineHeight: 1.6,
            }}
          >
            <div
              id={titleId}
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
          </div>,
          document.body,
        )}
    </div>
  );
}
