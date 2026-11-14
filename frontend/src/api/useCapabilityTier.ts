import { useAgencies } from "./hooks";
import type { CapabilityTier } from "./types";

/** The agency's capability tier from the catalogue every page already
 *  loads; undefined until it arrives or for an agency it doesn't list. */
export function useCapabilityTier(agencyId: number | null | undefined): CapabilityTier | undefined {
  return useAgencies().data?.find((a) => a.agency_id === agencyId)?.capability_tier;
}
