const STORAGE_KEY = "transit.admin.ackedAlerts";
const TTL_MS = 7 * 24 * 60 * 60 * 1000;

/** Deterministic 32-bit FNV-1a hash, rendered as 8 hex digits. Good enough to
 *  key a small localStorage map by level+text+href without a hashing
 *  dependency: a collision would only mis-acknowledge one alert, never
 *  corrupt the store. Keeping the pipe separator means "warn"+"a|b" and
 *  "warna"+"|b" hash differently rather than colliding on concatenation. */
export function hashAlertKey(level: string, text: string, href: string | null): string {
  const input = `${level}|${text}|${href ?? ""}`;
  let hash = 0x811c9dc5;
  for (let i = 0; i < input.length; i++) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

export type AckedMap = Record<string, number>;

/** Drops entries whose 7-day acknowledgement window has elapsed as of `now`,
 *  so a hash that recurs after expiry reads as unacknowledged again instead
 *  of the store growing forever. */
export function pruneAcked(map: AckedMap, now: number): AckedMap {
  const pruned: AckedMap = {};
  for (const [hash, expiresAt] of Object.entries(map)) {
    if (expiresAt > now) pruned[hash] = expiresAt;
  }
  return pruned;
}

function readRawMap(): AckedMap {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return {};
    const map: AckedMap = {};
    for (const [hash, expiresAt] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof expiresAt === "number") map[hash] = expiresAt;
    }
    return map;
  } catch {
    return {};
  }
}

function writeRawMap(map: AckedMap): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    /* localStorage unavailable (private browsing, quota) -- the alert simply
       never leaves the unread count in this browser, the safe direction for
       the count to be wrong in. */
  }
}

/** Hashes still inside their 7-day acknowledgement window, as of `now`.
 *  Expired and malformed entries are skipped here and dropped from storage
 *  on the next acknowledgement. */
export function readAckedAlerts(now: number): Set<string> {
  return new Set(Object.keys(pruneAcked(readRawMap(), now)));
}

/** The stored acknowledgements as written, for useSyncExternalStore: a
 *  string stays equal while the store is unchanged. */
export function ackedSnapshot(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

/** The hashes in `snapshot` still inside their window as of `now`. */
export function ackedFromSnapshot(snapshot: string, now: number): Set<string> {
  if (!snapshot) return new Set();
  try {
    const parsed: unknown = JSON.parse(snapshot);
    if (typeof parsed !== "object" || parsed === null) return new Set();
    const map: AckedMap = {};
    for (const [hash, expiresAt] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof expiresAt === "number") map[hash] = expiresAt;
    }
    return new Set(Object.keys(pruneAcked(map, now)));
  } catch {
    return new Set();
  }
}

const listeners = new Set<() => void>();

/** Calls `onChange` when acknowledgements change in this tab or another. */
export function subscribeAcked(onChange: () => void): () => void {
  const onStorage = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY || e.key === null) onChange();
  };
  listeners.add(onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onStorage);
  };
}

/** Marks `hash` acknowledged for 7 days from `now`. Server-side
 *  acknowledgement (visible to other operators/devices) is a follow-up --
 *  this only silences the alert in the browser that dismissed it. */
export function ackAlert(hash: string, now: number): void {
  const pruned = pruneAcked(readRawMap(), now);
  pruned[hash] = now + TTL_MS;
  writeRawMap(pruned);
  // A tab hears its own writes from here; other tabs from the storage event.
  for (const listener of listeners) listener();
}
