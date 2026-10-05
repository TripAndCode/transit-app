import { useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Link } from "react-router-dom";
import { Bell, X } from "lucide-react";
import { useFocusTrap } from "../../hooks/useFocusTrap";
import { useAdminBoard, type BoardAlert } from "../../api/admin";
import { ackAlert, hashAlertKey, readAckedAlerts } from "./ackedAlerts";
import "./alertCenter.css";

const LEVELS = ["warn", "info"] as const satisfies readonly BoardAlert["level"][];

function alertText(t: TFunction, alert: BoardAlert): string {
  return t(`admin.board.alert.${alert.code}`, { ...alert.params, defaultValue: alert.text });
}

/**
 * Header bell + popover for `/api/admin/board`'s `alerts`. Reads the same
 * `useAdminBoard()` query the board page polls, so opening this never starts
 * a second poll -- React Query dedupes on the shared `["adminBoard"]` key.
 *
 * Acknowledgement is client-side only (`ackedAlerts.ts`, localStorage keyed
 * by a hash of level+text+href, expiring after 7 days): it silences an alert
 * in the browser that dismissed it. Server-side acknowledgement, visible to
 * other operators, is a follow-up.
 */
export function AlertCenter() {
  const { t } = useTranslation();
  const { data } = useAdminBoard();
  const alerts = data?.alerts ?? [];
  const [open, setOpen] = useState(false);
  const [ackedHashes, setAckedHashes] = useState(() => readAckedAlerts(Date.now()));
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  function closePopover() {
    setOpen(false);
  }

  useFocusTrap(open, panelRef, closePopover);

  // Anchors the portaled panel under the trigger. Written straight to the
  // node in a layout effect (not held in state) since the measurement only
  // exists to place an already-mounted element -- a state round-trip would
  // re-render the trigger for it, the same reasoning Tooltip.tsx documents.
  useLayoutEffect(() => {
    if (!open) return;
    const button = buttonRef.current;
    const panel = panelRef.current;
    if (!button || !panel) return;
    const rect = button.getBoundingClientRect();
    panel.style.top = `${rect.bottom + 8}px`;
    panel.style.right = `${window.innerWidth - rect.right}px`;
  }, [open]);

  const hashed = alerts.map((alert) => ({ alert, hash: hashAlertKey(alert.level, alert.text, alert.href) }));
  const unreadCount = hashed.filter(({ hash }) => !ackedHashes.has(hash)).length;

  function openPopover() {
    setAckedHashes(readAckedAlerts(Date.now()));
    setOpen(true);
  }

  function acknowledge(hash: string) {
    const now = Date.now();
    ackAlert(hash, now);
    setAckedHashes(readAckedAlerts(now));
  }

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className="alert-center-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={
          unreadCount > 0
            ? t("admin.alert_center.bell_label_unread", { count: unreadCount })
            : t("admin.alert_center.bell_label")
        }
        onClick={() => (open ? closePopover() : openPopover())}
      >
        <Bell size={18} strokeWidth={1.6} aria-hidden="true" />
        {alerts.length > 0 && (
          <span
            aria-hidden="true"
            data-testid="alert-count-badge"
            className={unreadCount > 0 ? "alert-center-badge" : "alert-center-badge alert-center-badge--muted"}
          >
            {unreadCount}
          </span>
        )}
      </button>
      {open &&
        createPortal(
          <div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            tabIndex={-1}
            className="alert-center-panel ov-modal"
            style={{ position: "fixed", top: 0, right: 0 }}
          >
            <div className="alert-center-header">
              <h2 id={titleId} className="alert-center-title">
                {t("admin.alert_center.title")}
              </h2>
              <button
                type="button"
                className="alert-center-close"
                onClick={closePopover}
                aria-label={t("common.close")}
              >
                <X size={14} strokeWidth={1.75} aria-hidden="true" />
              </button>
            </div>
            {alerts.length === 0 ? (
              <p className="alert-center-empty">{t("admin.alert_center.empty")}</p>
            ) : (
              LEVELS.map((level) => {
                const group = hashed.filter(({ alert }) => alert.level === level);
                if (group.length === 0) return null;
                return (
                  <section key={level} className="alert-center-group">
                    <p className="alert-center-group-label">{t(`admin.alert_center.group.${level}`)}</p>
                    <ul className="alert-center-list">
                      {group.map(({ alert, hash }) => {
                        const isAcked = ackedHashes.has(hash);
                        return (
                          <li
                            key={hash}
                            className="alert-center-item"
                            data-testid="alert-center-item"
                            data-acked={isAcked}
                          >
                            <span className={`alert-center-pill alert-center-pill--${level}`}>
                              {t(`admin.alert_center.level.${level}`)}
                            </span>
                            <span className="alert-center-item-text">{alertText(t, alert)}</span>
                            {alert.href && (
                              <Link to={alert.href} className="alert-center-link" onClick={closePopover}>
                                {t("admin.board.alert_open")}
                              </Link>
                            )}
                            <button
                              type="button"
                              className="alert-center-ack"
                              disabled={isAcked}
                              onClick={() => acknowledge(hash)}
                            >
                              {isAcked ? t("admin.alert_center.acked") : t("admin.alert_center.ack")}
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  </section>
                );
              })
            )}
          </div>,
          document.body,
        )}
    </>
  );
}
