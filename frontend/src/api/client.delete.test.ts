import { afterEach, describe, expect, it, vi } from "vitest";
import { apiDelete } from "./client";

describe("apiDelete", () => {
  afterEach(() => vi.restoreAllMocks());

  it("sends a JSON body when one is given", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    await apiDelete("/api/me", { body: { confirm_email: "a@x" } });
    const init = fetchSpy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("DELETE");
    expect(init.body).toBe(JSON.stringify({ confirm_email: "a@x" }));
  });
});
