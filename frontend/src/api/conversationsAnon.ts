// localStorage-backed CRUD for anonymous threads.
// Cap: 20 threads. When full, the oldest non-pinned thread drops.

import type { AnonThread, ConvMessage, FilterCtx } from "./types";

const KEY = "ask:conversations:v1";
const MAX_THREADS = 20;
const MAX_MESSAGES_PER_THREAD = 20;

function read(): AnonThread[] {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as AnonThread[]) : [];
  } catch { return []; }
}
function write(threads: AnonThread[]): void {
  try { localStorage.setItem(KEY, JSON.stringify(threads)); } catch { /* quota */ }
}
function uuid(): string {
  // crypto.randomUUID is available in evergreen browsers
  return typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

export const conversationsAnon = {
  list(agency_id: number): AnonThread[] {
    return read()
      .filter((t) => t.agency_id === agency_id)
      .sort((a, b) => {
        if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
        return b.updated_at.localeCompare(a.updated_at);
      });
  },

  // `agency_id`, when given, scopes the lookup so a stale client_id from a
  // different agency can never resolve to another agency's thread.
  get(client_id: string, agency_id?: number): AnonThread | undefined {
    return read().find((t) => t.client_id === client_id && (agency_id === undefined || t.agency_id === agency_id));
  },

  create(agency_id: number, title: string, filter_ctx: FilterCtx = {}): AnonThread {
    const now = new Date().toISOString();
    const thread: AnonThread = {
      client_id: uuid(), agency_id, title: title.slice(0, 200), filter_ctx,
      pinned: false, created_at: now, updated_at: now, messages: [],
    };
    const all = read();
    all.unshift(thread);
    // Cap: keep all pinned + most recent non-pinned up to MAX_THREADS
    const pinned = all.filter((t) => t.pinned);
    const recent = all.filter((t) => !t.pinned).slice(0, Math.max(0, MAX_THREADS - pinned.length));
    write([...pinned, ...recent]);
    return thread;
  },

  update(client_id: string, patch: Partial<Pick<AnonThread, "title" | "pinned" | "filter_ctx">>): AnonThread | undefined {
    const all = read();
    const i = all.findIndex((t) => t.client_id === client_id);
    if (i < 0) return undefined;
    const next = { ...all[i], ...patch, updated_at: new Date().toISOString() };
    if (patch.title !== undefined) next.title = patch.title.slice(0, 200);
    all[i] = next;
    write(all);
    return next;
  },

  delete(client_id: string): void {
    write(read().filter((t) => t.client_id !== client_id));
  },

  appendMessage(client_id: string, msg: ConvMessage): AnonThread | undefined {
    const all = read();
    const i = all.findIndex((t) => t.client_id === client_id);
    if (i < 0) return undefined;
    const messages = [...all[i].messages, msg].slice(-MAX_MESSAGES_PER_THREAD);
    all[i] = { ...all[i], messages, updated_at: new Date().toISOString() };
    write(all);
    return all[i];
  },

  exportAll(): AnonThread[] { return read(); },

  clearAll(): void { localStorage.removeItem(KEY); },
};
