import type { ReactNode } from "react";
import { useAgencies } from "./hooks";
import { DataEndContext } from "./scope";
import { useAgencyId } from "./useAgencyId";

/** Gives every useScope below it the current agency's latest data day. */
export function AgencyDataEndProvider({ children }: { children: ReactNode }) {
  const agencyId = useAgencyId();
  const { data: agencies } = useAgencies();
  const dataEnd = agencies?.find((a) => a.agency_id === agencyId)?.latest_data_date ?? null;
  return <DataEndContext value={dataEnd}>{children}</DataEndContext>;
}
