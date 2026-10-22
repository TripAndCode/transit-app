import { scopeToQueryString, type Scope } from "../../api/scope";
import { uuid } from "../../utils/uuid";
import { withStorage, writeStorage } from "../../utils/safeStorage";

type SavedAnalysis = { id: string; agencyId: number; title: string; query: string; savedAt: string };
const STORAGE_ID = "transit.savedAnalyses.v1";
export function readAnalyses(): SavedAnalysis[] {
  return withStorage("localStorage", (storage) => {
    const value: unknown = JSON.parse(storage.getItem(STORAGE_ID) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((v): v is SavedAnalysis => v && typeof v.id === "string" && Number.isInteger(v.agencyId) && typeof v.title === "string" && typeof v.query === "string" && typeof v.savedAt === "string");
  }, []);
}

/** Saves an analysis. Returns whether it was persisted -- `false` (rather
 *  than throwing) when localStorage is unavailable, e.g. private browsing
 *  or a full quota. */
export function saveAnalysis(agencyId: number, title: string, ctx: Scope, compare: boolean): boolean {
  const params = new URLSearchParams(scopeToQueryString(ctx));
  if (compare) params.set("compare", "1");
  const query = params.toString();
  const rows = readAnalyses().filter((r) => r.agencyId !== agencyId || r.query !== query);
  return writeStorage("localStorage", STORAGE_ID, JSON.stringify([{ id: uuid(), agencyId, title, query, savedAt: new Date().toISOString() }, ...rows].slice(0, 100)));
}

/** Deletes a saved analysis. Returns whether the write succeeded, same
 *  caveat as `saveAnalysis`. */
export function deleteAnalysis(id: string): boolean {
  return writeStorage("localStorage", STORAGE_ID, JSON.stringify(readAnalyses().filter((r) => r.id !== id)));
}
