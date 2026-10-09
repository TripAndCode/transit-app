import { useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigationType } from "react-router-dom";
import { Bell, X } from "lucide-react";
import { OverlayBase } from "../../components/ui/OverlayBase";
import { Z_INDEX } from "../../styles/zIndex";
import { useAckBoardAlert, useAdminBoard, type BoardAlert } from "../../api/admin";
import { alertText } from "./alertText";
import "./alertCenter.css";

const LEVELS = ["warn", "info"] as const satisfies readonly BoardAlert["level"][];

function withKey(keys: ReadonlySet<string>, key: string, present: boolean): ReadonlySet<string> {
  const next = new Set(keys);
  if (present) next.add(key);
  else next.delete(key);
  return next;
}

/** Off the board page the bell is the only reader of the snapshot, and an
 *  alert does not need the board's own pace to be noticed. */
const BELL_POLL_MS = 60_000;

/**
 * Header bell + popover for `/api/admin/board`'s `alerts`, on every admin
 * page. On the board page, whose own subscriber already polls, the bell adds
 * no timer; elsewhere it polls at `BELL_POLL_MS`.
 *
 * Acknowledging an alert silences it for every admin, on every device, for a
 * week; the server stores it, and the board's poll carries it back.
 */
export function AlertCenter() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigationType = useNavigationType();
  const onBoard = location.pathname === "/admin" || location.pathname === "/admin/";
  const board = useAdminBoard({ refetchInterval: onBoard ? false : BELL_POLL_MS });
  const alerts = board.data?.alerts ?? [];
  const [open, setOpen] = useState(false);
  // A navigation closes it, history steps included; a page rewriting its own
  // query string in place (a debounced search) does not. Reset during render
  // from the location it last saw rather than synchronised in an effect.
  const [seen, setSeen] = useState({ key: location.key, pathname: location.pathname });
  if (seen.key !== location.key) {
    setSeen({ key: location.key, pathname: location.pathname });
    if (location.pathname !== seen.pathname || navigationType !== "REPLACE") setOpen(false);
  }
  const [anchor, setAnchor] = useState({ top: 0, right: 0 });
  const buttonRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const ack = useAckBoardAlert();
  // Kept per alert, from each acknowledgement's own promise: the mutation's
  // own state follows only its latest call, so a second alert acknowledged
  // meanwhile would otherwise release the first one's button and hide its
  // failure.
  const [sending, setSending] = useState<ReadonlySet<string>>(() => new Set());
  const [failed, setFailed] = useState<ReadonlySet<string>>(() => new Set());

  function acknowledge(key: string) {
    setSending((keys) => withKey(keys, key, true));
    setFailed((keys) => withKey(keys, key, false));
    ack
      .mutateAsync(key)
      .catch(() => setFailed((keys) => withKey(keys, key, true)))
      .finally(() => setSending((keys) => withKey(keys, key, false)));
  }

  const unread = alerts.filter((alert) => !alert.acked);
  const warnUnread = unread.some((alert) => alert.level === "warn");

  function close() {
    setOpen(false);
  }

  function toggle() {
    if (open) {
      close();
      return;
    }
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) setAnchor({ top: rect.bottom + 8, right: window.innerWidth - rect.right });
    setOpen(true);
  }

  const badgeClass = [
    "alert-center-badge",
    unread.length === 0 ? "alert-center-badge--muted" : warnUnread ? "alert-center-badge--warn" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className="alert-center-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={
          unread.length > 0
            ? t("admin.alert_center.bell_label_unread", { count: unread.length })
            : t("admin.alert_center.bell_label")
        }
        onClick={toggle}
      >
        <Bell size={18} strokeWidth={1.6} aria-hidden="true" />
        {alerts.length > 0 && (
          <span aria-hidden="true" data-testid="alert-count-badge" className={badgeClass}>
            {unread.length}
          </span>
        )}
      </button>
      <OverlayBase
        open={open}
        onClose={close}
        labelledBy={titleId}
        zIndex={Z_INDEX.modal}
        scrimZIndex={Z_INDEX.modalBackdrop}
        initialFocus="panel"
        className="alert-center-panel"
        style={{ position: "fixed", top: anchor.top, right: anchor.right }}
      >
        <div className="alert-center-header">
          <h2 id={titleId} className="alert-center-title">
            {t("admin.alert_center.title")}
          </h2>
          <button type="button" className="alert-center-close" onClick={close} aria-label={t("common.close")}>
            <X size={14} strokeWidth={1.75} aria-hidden="true" />
          </button>
        </div>
        {board.data && board.error && <p className="alert-center-stale">{t("admin.alert_center.refresh_failed")}</p>}
        {!board.data ? (
          // Loading or failed is not the same as nothing to report.
          <p className="alert-center-empty">{board.error ? t("admin.alert_center.load_failed") : t("common.loading")}</p>
        ) : alerts.length === 0 ? (
          <p className="alert-center-empty">{t("admin.board.alerts_none")}</p>
        ) : (
          LEVELS.map((level) => {
            const group = alerts.filter((alert) => alert.level === level);
            if (group.length === 0) return null;
            return (
              <section key={level} className="alert-center-group">
                <p className="alert-center-group-label">{t(`admin.alert_center.group.${level}`)}</p>
                <ul className="alert-center-list">
                  {group.map((alert) => (
                      <li key={alert.key} className="alert-center-item" data-testid="alert-center-item" data-acked={alert.acked}>
                        <span className="alert-center-item-text">{alertText(t, alert)}</span>
                        {alert.href && (
                          <Link to={alert.href} className="alert-center-link">
                            {t("admin.board.alert_open")}
                          </Link>
                        )}
                        <button
                          type="button"
                          className="alert-center-ack"
                          disabled={alert.acked || sending.has(alert.key)}
                          onClick={() => acknowledge(alert.key)}
                        >
                          {alert.acked ? t("admin.alert_center.acked") : t("admin.alert_center.ack")}
                        </button>
                        {failed.has(alert.key) && <p className="alert-center-ack-failed">{t("admin.alert_center.ack_failed")}</p>}
                      </li>
                  ))}
                </ul>
              </section>
            );
          })
        )}
      </OverlayBase>
    </>
  );
}
