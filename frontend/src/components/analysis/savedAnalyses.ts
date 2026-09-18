import { ctxToQueryString, type RangeCtx } from "../../api/rangeContext";

type SavedAnalysis = { id: string; agencyId: number; title: string; query: string; savedAt: string };
const STORAGE_ID = "transit.savedAnalyses.v1";
export function readAnalyses(): SavedAnalysis[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(STORAGE_ID) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((v): v is SavedAnalysis => v && typeof v.id === "string" && Number.isInteger(v.agencyId) && typeof v.title === "string" && typeof v.query === "string" && typeof v.savedAt === "string");
  } catch { return []; }
}
export function saveAnalysis(agencyId: number, title: string, ctx: RangeCtx, compare: boolean) {
  const params = new URLSearchParams(ctxToQueryString(ctx));
  if (compare) params.set("compare", "1");
  const query = params.toString();
  const rows = readAnalyses().filter((r) => r.agencyId !== agencyId || r.query !== query);
  localStorage.setItem(STORAGE_ID, JSON.stringify([{ id: crypto.randomUUID(), agencyId, title, query, savedAt: new Date().toISOString() }, ...rows].slice(0, 100)));
}
export function deleteAnalysis(id: string) {
  localStorage.setItem(STORAGE_ID, JSON.stringify(readAnalyses().filter((r) => r.id !== id)));
}
