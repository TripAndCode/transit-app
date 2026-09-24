import { useEffect, useEffectEvent, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ToastCtx, type ToastAction, type ToastOptions } from "./toastContext";
import "./ui.css";

/** How long a toast stands before it retires itself. Long enough to read a
 *  sentence and reach for an undo, short enough that a stack of them never
 *  becomes furniture. Paused while the pointer or keyboard focus is on the
 *  toast, so the window is reading time, not wall time. */
const DEFAULT_DURATION_MS = 6_000;

type ToastRecord = {
  id: number;
  message: string;
  action?: ToastAction;
  durationMs: number;
};

/**
 * The app's transient-confirmation channel: one place that owns the stacking
 * rung, the auto-dismiss window, and the pause-while-read behaviour, so a
 * page announcing "saved" or offering an undo never hand-rolls a fixed box of
 * its own.
 *
 * Notifications are advisory, never the only record of what happened: a toast
 * disappears on its own, so anything the operator must still be able to act on
 * later belongs in the page, not here.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  const nextIdRef = useRef(1);

  function dismiss(id: number) {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }

  function show(message: string, options?: ToastOptions): number {
    const id = nextIdRef.current++;
    setToasts((current) => [
      ...current,
      { id, message, action: options?.action, durationMs: options?.durationMs ?? DEFAULT_DURATION_MS },
    ]);
    return id;
  }

  return (
    <ToastCtx.Provider value={{ show, dismiss }}>
      {children}
      {toasts.length > 0 &&
        createPortal(
          <div className="ui-toast-viewport">
            {toasts.map((toast) => (
              <ToastItem key={toast.id} toast={toast} onDismiss={dismiss} />
            ))}
          </div>,
          document.body,
        )}
    </ToastCtx.Provider>
  );
}

function ToastItem({ toast, onDismiss }: { toast: ToastRecord; onDismiss: (id: number) => void }) {
  const [paused, setPaused] = useState(false);
  // What is left of the window, carried across pauses: restarting the full
  // duration on every pointer-out would let a toast the reader keeps brushing
  // past outlive the message it carries.
  const remainingRef = useRef(toast.durationMs);
  const retire = useEffectEvent(() => onDismiss(toast.id));

  useEffect(() => {
    if (paused) return;
    const startedAt = Date.now();
    const timer = setTimeout(retire, remainingRef.current);
    return () => {
      clearTimeout(timer);
      remainingRef.current = Math.max(0, remainingRef.current - (Date.now() - startedAt));
    };
  }, [paused]);

  return (
    <div
      className="ui-toast"
      role="status"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <span className="ui-toast__message">{toast.message}</span>
      {toast.action && (
        <button
          type="button"
          className="ui-toast__action"
          onClick={() => {
            toast.action?.onClick();
            onDismiss(toast.id);
          }}
        >
          {toast.action.label}
        </button>
      )}
    </div>
  );
}
