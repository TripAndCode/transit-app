import { useAgencies } from "./hooks";
import type { CapabilityTier } from "./types";

/** An agency's tier as a page knows it: `loading` until the catalogue
 *  arrives, when a page words no reason the tier might replace; null when
 *  there is no tier to state (the catalogue failed or doesn't list the
 *  agency), when the pipeline's own reason stands. */
export type TierState = CapabilityTier | "loading" | null;

/** Each agency's tier from the catalogue every page already loads. */
export function useCapabilityTierOf(): (agencyId: number | null | undefined) => TierState {
  const { data, isPending } = useAgencies();
  return (agencyId) => {
    if (data == null) return isPending ? "loading" : null;
    return data.find((a) => a.agency_id === agencyId)?.capability_tier ?? null;
  };
}

export function useCapabilityTier(agencyId: number | null | undefined): TierState {
  return useCapabilityTierOf()(agencyId);
}
