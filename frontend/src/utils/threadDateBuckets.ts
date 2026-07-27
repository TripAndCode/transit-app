import { isoDaysAgo, todayISO, toJstISO } from "../api/rangeContext";

// Day-bucketing pins JST like every other date boundary in the app
// (rangeContext.ts, RangeBadge.tsx) — comparing in the viewer's local
// timezone would put a conversation in the wrong day near midnight JST for
// anyone not on a JST machine (e.g. Singapore-based staff).
export function isToday(iso: string): boolean {
  return toJstISO(new Date(iso)) === todayISO();
}

export function isYesterday(iso: string): boolean {
  return toJstISO(new Date(iso)) === isoDaysAgo(1);
}
