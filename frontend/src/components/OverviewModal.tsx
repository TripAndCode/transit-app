import { useTranslation } from "react-i18next";
// Self-sufficient styling: this shared modal is used outside OverviewTab
// (e.g. RouteForecastSection), so it must carry its own `.ov-modal-*` rules
// rather than rely on a tab chunk having loaded overview.css first.
import "../styles/overview.css";
import { Modal } from "./Modal";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
};

/** Calm-UI modal used for click-to-expand drill-down on every Overview
 *  card. Escape, backdrop click, focus trap/restore and body-scroll lock
 *  come from the shared Modal. Entry animation (`.ov-modal`'s keyframe)
 *  is gated by ``prefers-reduced-motion``.
 */
export function OverviewModal({ isOpen, onClose, title, children }: Props) {
  const { t } = useTranslation();

  return (
    <Modal open={isOpen} onClose={onClose} ariaLabel={title} className="ov-modal">
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
    </Modal>
  );
}
