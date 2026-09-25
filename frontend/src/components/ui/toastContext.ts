import { createContext, useContext } from "react";

export type ToastAction = {
  /** Already-translated label. */
  label: string;
  onClick: () => void;
};

export type ToastOptions = {
  action?: ToastAction;
  /** Overrides the default window. Use only where the action behind the
   *  toast needs a longer grace period than reading time (e.g. a bulk undo). */
  durationMs?: number;
};

export type ToastApi = {
  /** Shows `message` (already translated) and returns the toast's id. */
  show: (message: string, options?: ToastOptions) => number;
  dismiss: (id: number) => void;
};

/** Split from `Toast.tsx` so that file exports components only — the shape
 *  Fast Refresh needs to hot-swap the provider without losing state. */
export const ToastCtx = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const api = useContext(ToastCtx);
  if (api === null) throw new Error("useToast requires a <ToastProvider> above it");
  return api;
}
