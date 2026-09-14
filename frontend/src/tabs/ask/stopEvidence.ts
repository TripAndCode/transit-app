import type { ConvMessage } from "../../api/types";

export type StopEvidence = { sequence: number; name: string; minutes: number | null; samples: number;
  patternId?: string; patternName?: string; stopId?: string; rowIndex?: number };
export type StopFocus = { messageId: number; sequence: number; name: string;
  patternId?: string; stopId?: string; rowIndex?: number };

export function stopEvidence(message: ConvMessage): StopEvidence[] | null {
  const result = message.result;
  const complete = message.tool === "route_stop_patterns";
  if (message.role !== "assistant" || (!complete && message.tool !== "segment_hotspots") || result?.kind !== "table") return null;
  const columns = result.columns ?? [];
  const indices = ["stop_sequence", "stop_name", "avg_min", "samples"].map((key) => columns.indexOf(key));
  if (indices.includes(-1) || !result.rows?.length) return null;
  const points: StopEvidence[] = [];
  for (const [rowIndex, row] of result.rows.entries()) {
    if (!Array.isArray(row)) return null;
    const [sequence, name, minutes, samples] = indices.map((index) => row[index]);
    if (!Number.isInteger(sequence) || sequence < 0 || typeof name !== "string" ||
      !Number.isInteger(samples) || samples < 0) return null;
    if (minutes === null ? !complete || samples !== 0 : typeof minutes !== "number" || !Number.isFinite(minutes)) return null;
    const patternId = row[columns.indexOf("pattern_id")];
    const patternName = row[columns.indexOf("pattern_name")];
    const stopId = row[columns.indexOf("stop_id")];
    if (complete && (typeof patternId !== "string" || !patternId || typeof patternName !== "string" ||
      typeof stopId !== "string" || !stopId || (minutes !== null && samples === 0))) return null;
    if (points.some((point) => point.sequence === sequence && (!complete || point.patternId === patternId))) return null;
    points.push({ sequence, name, minutes, samples,
      ...(complete ? { patternId, patternName, stopId, rowIndex } : {}) });
  }
  return points;
}
