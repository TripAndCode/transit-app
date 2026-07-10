// frontend/src/components/OverviewModal.tsx
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
// Self-sufficient styling: this shared modal is used outside OverviewTab
// (e.g. RouteForecastSection), so it must carry its own `.ov-modal-*` rules
// rather than rely on a tab chunk having loaded overview.css first.
import "../styles/overview.css";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
};

/** Calm-UI modal used for click-to-expand drill-down on every Overview
 *  card. Escape and backdrop click both close. Body scroll is locked
 *  while open. Entry animation gated by ``prefers-reduced-motion``.
 */
export function OverviewModal({ isOpen, onClose, title, children }: Props) {
  const { t } = useTranslation();

  useEffect(() => {
    if (!isOpen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div
      className="ov-modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="presentation"
    >
      <div
        className="ov-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="ov-modal-header">
          <h2 className="ov-modal-title">{title}</h2>
          <button
            type="button"
            className="ov-modal-close"
            onClick={onClose}
            aria-label={t("common.close")}
          >
            ×
          </button>
        </div>
        <div className="ov-modal-body">{children}</div>
      </div>
    </div>
  );
}
