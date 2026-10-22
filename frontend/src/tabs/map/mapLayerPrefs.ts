import { useState } from "react";
import { readStorage, writeStorage } from "../../utils/safeStorage";

export const RELIEF_PREF_KEY = "transit.mapRelief";
export const PEARL_PREF_KEY = "transit.mapPearl";
export const LIGHT_PREF_KEY = "transit.mapLight";

/** A boolean map-layer preference in localStorage, "1"/"0". Unavailable
 *  storage reads as the fallback and swallows the write, like the style
 *  and dim preferences in styles/mapStyle.ts. */
export function readBoolPref(key: string, fallback: boolean): boolean {
  const v = readStorage("localStorage", key);
  if (v === "1") return true;
  if (v === "0") return false;
  return fallback;
}

export function writeBoolPref(key: string, value: boolean): void {
  writeStorage("localStorage", key, value ? "1" : "0");
}

/** Current value + a setter that also persists, mirroring useMapStylePref. */
export function useBoolPref(key: string, fallback: boolean): [boolean, (next: boolean) => void] {
  const [value, setValue] = useState(() => readBoolPref(key, fallback));
  return [
    value,
    (next) => {
      writeBoolPref(key, next);
      setValue(next);
    },
  ];
}
