import type { FilterCtx } from "./types";

/** Writes one thread's filter: a PATCH for an account's thread, a
 *  localStorage write for a signed-out reader's. */
export type FilterWrite = (filter: FilterCtx) => Promise<unknown>;

type Save = { filter: FilterCtx; write: FilterWrite };

type ThreadSaves = {
  running: Save | null;
  /** The one value to send after the running save: a newer edit replaces it. */
  next: Save | null;
  /** The last save, when it failed and nothing has been saved since. */
  failed: { save: Save; error: unknown } | null;
  /** Deleted: nothing more is sent, and the running save's result is ignored. */
  cancelled: boolean;
  waiters: { resolve: () => void; reject: (error: unknown) => void }[];
  onIdle: (() => void) | null;
};

/** Module state rather than a component's, so a screen that remounts while a
 *  save is in flight queues behind it instead of racing it. */
const threads = new Map<string, ThreadSaves>();

function stateFor(threadId: string): ThreadSaves {
  let state = threads.get(threadId);
  if (!state) {
    state = { running: null, next: null, failed: null, cancelled: false, waiters: [], onIdle: null };
    threads.set(threadId, state);
  }
  return state;
}

function settleWaiters(state: ThreadSaves) {
  const waiters = state.waiters.splice(0);
  for (const waiter of waiters) {
    if (state.failed && !state.cancelled) waiter.reject(state.failed.error);
    else waiter.resolve();
  }
}

function start(threadId: string, state: ThreadSaves, save: Save) {
  state.running = save;
  void save
    .write(save.filter)
    .then(
      () => {
        state.failed = null;
      },
      (error: unknown) => {
        state.failed = { save, error };
      },
    )
    .then(() => {
      state.running = null;
      const next = state.next;
      state.next = null;
      if (next && !state.cancelled) {
        start(threadId, state, next);
        return;
      }
      settleWaiters(state);
      if (state.cancelled) threads.delete(threadId);
      else if (!state.failed) state.onIdle?.();
    });
}

/** Saves a thread's filter as it is edited. With no save in flight for the
 *  thread it starts one; otherwise the value waits as the thread's single
 *  next save, replacing any earlier one. A burst of edits is therefore at
 *  most two writes, in order, the last carrying the latest value. `onIdle`
 *  runs once the thread's last save has landed with nothing queued. */
export function saveThreadFilter(threadId: string, filter: FilterCtx, write: FilterWrite, onIdle?: () => void): void {
  const state = stateFor(threadId);
  state.cancelled = false;
  if (onIdle) state.onIdle = onIdle;
  const save = { filter, write };
  if (state.running) state.next = save;
  else start(threadId, state, save);
}

/** Resolves once nothing is in flight or queued for the thread, and rejects
 *  when its last save failed. A save that failed before is sent again first,
 *  so each dispatch retries it. */
export function flushThreadFilter(threadId: string): Promise<void> {
  const state = threads.get(threadId);
  if (!state || state.cancelled) return Promise.resolve();
  if (!state.running && state.failed) start(threadId, state, state.failed.save);
  if (!state.running) return Promise.resolve();
  return new Promise((resolve, reject) => state.waiters.push({ resolve, reject }));
}

/** The filter a thread's saves will leave it with, while any is in flight or
 *  queued or the last one failed; null once its stored filter is current. */
export function pendingThreadFilter(threadId: string): FilterCtx | null {
  const state = threads.get(threadId);
  if (!state || state.cancelled) return null;
  return (state.next ?? state.running ?? state.failed?.save)?.filter ?? null;
}

/** A deleted thread: its queued save is dropped and its running one's
 *  outcome ignored, so nothing reports a failure for a thread that is gone. */
export function cancelThreadFilter(threadId: string): void {
  const state = threads.get(threadId);
  if (!state) return;
  state.next = null;
  state.failed = null;
  state.cancelled = true;
  if (!state.running) {
    settleWaiters(state);
    threads.delete(threadId);
  }
}

/** Test seam: forget every thread's saves. */
export function resetThreadFilterSaves(): void {
  threads.clear();
}
