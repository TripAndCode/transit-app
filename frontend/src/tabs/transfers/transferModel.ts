import type { RevisionDayType, TransferPairSummary, TransferTally } from "../../api/types";

/** The pair `requested` names, else the first listed, else null. */
export function pickPair(pairs: TransferPairSummary[], requested: string | null): TransferPairSummary | null {
  return pairs.find((p) => String(p.id) === requested) ?? pairs[0] ?? null;
}

/** The requested day type when the pair is read on it, else its first. */
export function pickDayType(pair: TransferPairSummary, requested: string | null): RevisionDayType {
  return pair.day_types.find((d) => d === requested) ?? pair.day_types[0];
}

/** Held, missed and unobserved as percentages of the connections planned,
 *  for a bar drawn to the planned total. */
export function outcomeWidths(t: TransferTally): { held: number; missed: number; unobserved: number } {
  if (t.planned === 0) return { held: 0, missed: 0, unobserved: 0 };
  return {
    held: (t.held / t.planned) * 100,
    missed: (t.missed / t.planned) * 100,
    unobserved: (t.unobserved / t.planned) * 100,
  };
}

/** Seconds as whole minutes, rounded to the nearest. */
export function minutes(sec: number): number {
  return Math.round(sec / 60);
}
