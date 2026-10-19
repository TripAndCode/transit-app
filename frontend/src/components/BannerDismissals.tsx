import { useState, type ReactNode } from "react";
import { DismissalsContext, type Dismissals } from "./bannerDismissalsContext";

/**
 * Which agencies each shell banner was dismissed for. Mounted above the router,
 * so a dismissal outlasts every route change and app-shell remount of one page
 * load, and a full reload starts empty: a dismissed warning is never hidden for
 * good. It is not in storage, so a browser that blocks site data cannot break it.
 */
export function BannerDismissalsProvider({ children }: { children: ReactNode }) {
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(() => new Set());
  const value: Dismissals = {
    isDismissed: (kind, agencyId) => dismissed.has(`${kind}:${agencyId}`),
    dismiss: (kind, agencyId) => setDismissed(new Set(dismissed).add(`${kind}:${agencyId}`)),
  };
  return <DismissalsContext.Provider value={value}>{children}</DismissalsContext.Provider>;
}
