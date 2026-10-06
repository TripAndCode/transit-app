import { useState } from "react";
import { useMatch, useParams } from "react-router-dom";
import { readLastAgency } from "./lastAgency";

/** The agency the rail's destinations lead to: the one in the URL; outside
 *  any agency, such as on Help or the account page, the one this visit last
 *  showed, else the last one chosen, so the way back stays one click. None
 *  on the agency picker, where choosing one is the point. */
export function useRailAgencyId(): string | undefined {
  const { agencyId } = useParams();
  const onPicker = useMatch("/") != null;
  const [lastSeen, setLastSeen] = useState(agencyId);
  if (agencyId != null && agencyId !== lastSeen) setLastSeen(agencyId);
  const [stored] = useState(() => readLastAgency());
  if (onPicker) return undefined;
  return agencyId ?? lastSeen ?? (stored != null ? String(stored) : undefined);
}
