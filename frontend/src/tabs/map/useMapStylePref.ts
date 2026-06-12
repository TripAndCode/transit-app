import { useState } from "react";
import { type MapStyleId, readMapStylePref, writeMapStylePref } from "../../styles/mapStyle";

/** Current map style id + a setter that also persists to localStorage. */
export function useMapStylePref(): [MapStyleId, (id: MapStyleId) => void] {
  const [id, setId] = useState<MapStyleId>(readMapStylePref);
  const set = (next: MapStyleId) => {
    writeMapStylePref(next);
    setId(next);
  };
  return [id, set];
}
