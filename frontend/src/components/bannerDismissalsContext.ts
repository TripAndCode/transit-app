import { createContext, useContext } from "react";

export type BannerKind = "data_stale" | "feed_health";

export type Dismissals = {
  isDismissed: (kind: BannerKind, agencyId: number) => boolean;
  dismiss: (kind: BannerKind, agencyId: number) => void;
};

/** Filled by BannerDismissalsProvider. */
export const DismissalsContext = createContext<Dismissals | null>(null);

/** Whether `kind` is dismissed for `agencyId`, and how to dismiss it there. */
export function useBannerDismissal(kind: BannerKind, agencyId: number | null): [boolean, () => void] {
  const dismissals = useContext(DismissalsContext);
  if (!dismissals) throw new Error("useBannerDismissal needs a BannerDismissalsProvider above it");
  return [
    agencyId != null && dismissals.isDismissed(kind, agencyId),
    () => {
      if (agencyId != null) dismissals.dismiss(kind, agencyId);
    },
  ];
}
