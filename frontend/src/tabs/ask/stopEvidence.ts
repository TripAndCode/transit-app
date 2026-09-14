import type { ConvMessage } from "../../api/types";

export type StopEvidence = { sequence: number; name: string; minutes: number; samples: number };
export type StopFocus = { messageId: number; sequence: number; name: string };

export function stopEvidence(message: ConvMessage): StopEvidence[] | null {
  const result = message.result;
  if (message.role !== "assistant" || message.tool !== "segment_hotspots" || result?.kind !== "table") return null;
  const columns = result.columns ?? [];
  const indices = ["stop_sequence", "stop_name", "avg_min", "samples"].map((key) => columns.indexOf(key));
  if (indices.includes(-1) || !result.rows?.length) return null;
  const points: StopEvidence[] = [];
  for (const row of result.rows) {
    if (!Array.isArray(row)) return null;
    const [sequence, name, minutes, samples] = indices.map((index) => row[index]);
    if (!Number.isInteger(sequence) || sequence < 0 || typeof name !== "string" ||
      typeof minutes !== "number" || !Number.isFinite(minutes) || !Number.isInteger(samples) || samples < 0) return null;
    if (points.some((point) => point.sequence === sequence)) return null;
    points.push({ sequence, name, minutes, samples });
  }
  return points;
}
