import { useState } from "react";

export const RELIEF_PREF_KEY = "transit.mapRelief";

/** A boolean map-layer preference in localStorage, "1"/"0". Unavailable
 *  storage reads as the fallback and swallows the write, like the style
 *  and dim preferences in styles/mapStyle.ts. */
export function readBoolPref(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key);
    if (v === "1") return true;
    if (v === "0") return false;
  } catch {
    /* localStorage unavailable -- fall through */
  }
  return fallback;
}

export function writeBoolPref(key: string, value: boolean): void {
  try {
    localStorage.setItem(key, value ? "1" : "0");
  } catch {
    /* localStorage unavailable -- the preference lasts this visit only */
  }
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
