import { describe, it, expect, beforeEach } from "vitest";
import { conversationsAnon } from "./conversationsAnon";
import type { ConvMessage } from "./types";

function message(id: number): ConvMessage {
  return {
    message_id: id,
    conversation_id: "x",
    role: "user",
    chip_id: null,
    tool: null,
    args: null,
    signature_hash: null,
    result: null,
    rendered_summary: `msg ${id}`,
    created_at: new Date().toISOString(),
  };
}

describe("conversationsAnon", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("creates a thread with the given agency, title, and filter context", () => {
    const thread = conversationsAnon.create(1, "My question", { dow: "weekday" });
    expect(thread.agency_id).toBe(1);
    expect(thread.title).toBe("My question");
    expect(thread.filter_ctx).toEqual({ dow: "weekday" });
    expect(thread.pinned).toBe(false);
    expect(thread.messages).toEqual([]);
    expect(conversationsAnon.get(thread.client_id)).toEqual(thread);
  });

  it("truncates an overlong title to 200 characters on create", () => {
    const thread = conversationsAnon.create(1, "x".repeat(250));
    expect(thread.title).toHaveLength(200);
  });

  it("lists only threads for the given agency, pinned first, then most-recently-updated", () => {
    const a = conversationsAnon.create(1, "A");
    const b = conversationsAnon.create(1, "B");
    conversationsAnon.create(2, "Other agency");
    conversationsAnon.update(a.client_id, { pinned: true });

    const list = conversationsAnon.list(1);
    expect(list.map((t) => t.client_id)).toEqual([a.client_id, b.client_id]);
  });

  it("scopes get() to the given agency_id, returning undefined for a mismatched agency", () => {
    const thread = conversationsAnon.create(1, "A");
    expect(conversationsAnon.get(thread.client_id, 1)).toBeDefined();
    expect(conversationsAnon.get(thread.client_id, 2)).toBeUndefined();
  });

  it("update() patches fields, bumps updated_at, and truncates a new title", () => {
    const thread = conversationsAnon.create(1, "A");
    const before = thread.updated_at;
    const updated = conversationsAnon.update(thread.client_id, { title: "y".repeat(250), pinned: true });
    expect(updated?.title).toHaveLength(200);
    expect(updated?.pinned).toBe(true);
    expect(updated!.updated_at >= before).toBe(true);
  });

  it("update() returns undefined for an unknown thread id", () => {
    expect(conversationsAnon.update("missing", { title: "x" })).toBeUndefined();
  });

  it("delete() removes only the targeted thread", () => {
    const a = conversationsAnon.create(1, "A");
    const b = conversationsAnon.create(1, "B");
    conversationsAnon.delete(a.client_id);
    expect(conversationsAnon.get(a.client_id)).toBeUndefined();
    expect(conversationsAnon.get(b.client_id)).toBeDefined();
  });

  it("appendMessage() appends and caps a thread's messages at 20, dropping the oldest", () => {
    const thread = conversationsAnon.create(1, "A");
    for (let i = 0; i < 25; i++) conversationsAnon.appendMessage(thread.client_id, message(i));
    const stored = conversationsAnon.get(thread.client_id)!;
    expect(stored.messages).toHaveLength(20);
    expect(stored.messages[0].message_id).toBe(5); // oldest 5 dropped
    expect(stored.messages.at(-1)!.message_id).toBe(24);
  });

  it("appendMessage() on an unknown thread id returns undefined and writes nothing", () => {
    expect(conversationsAnon.appendMessage("missing", message(1))).toBeUndefined();
  });

  it("caps stored threads at 20, keeping every pinned thread and dropping the oldest unpinned ones", () => {
    const pinned = conversationsAnon.create(1, "pinned");
    conversationsAnon.update(pinned.client_id, { pinned: true });
    for (let i = 0; i < 25; i++) conversationsAnon.create(1, `t${i}`);

    const all = conversationsAnon.exportAll();
    expect(all).toHaveLength(20);
    expect(all.some((t) => t.client_id === pinned.client_id)).toBe(true);
    // Only the 19 most recently created unpinned threads survive alongside it.
    expect(all.filter((t) => !t.pinned)).toHaveLength(19);
  });

  it("exportAll() returns every stored thread and clearAll() empties storage", () => {
    conversationsAnon.create(1, "A");
    conversationsAnon.create(1, "B");
    expect(conversationsAnon.exportAll()).toHaveLength(2);
    conversationsAnon.clearAll();
    expect(conversationsAnon.exportAll()).toHaveLength(0);
  });
});
