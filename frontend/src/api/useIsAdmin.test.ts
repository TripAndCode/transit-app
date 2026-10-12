import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useIsAdmin } from "./useIsAdmin";

let config: { auth_enabled: boolean; local_admin_enabled: boolean } | undefined;
let session: { role: "admin" | "user" } | undefined;

vi.mock("./config", () => ({ useConfig: () => ({ data: config }) }));
vi.mock("./auth", () => ({ useSession: () => ({ data: session }) }));

beforeEach(() => {
  config = { auth_enabled: false, local_admin_enabled: false };
  session = { role: "admin" };
});

describe("useIsAdmin", () => {
  it("is true for an admin when SSO is configured", () => {
    config = { auth_enabled: true, local_admin_enabled: false };
    expect(renderHook(() => useIsAdmin()).result.current).toBe(true);
  });

  it("is true for a local break-glass admin when SSO is off", () => {
    config = { auth_enabled: false, local_admin_enabled: true };
    expect(renderHook(() => useIsAdmin()).result.current).toBe(true);
  });

  it("is false when no sign-in method is configured, whatever a stale session says", () => {
    expect(renderHook(() => useIsAdmin()).result.current).toBe(false);
  });

  it("is false for a non-admin session", () => {
    config = { auth_enabled: true, local_admin_enabled: true };
    session = { role: "user" };
    expect(renderHook(() => useIsAdmin()).result.current).toBe(false);
  });

  it("is false before config or session has loaded", () => {
    config = undefined;
    expect(renderHook(() => useIsAdmin()).result.current).toBe(false);
    config = { auth_enabled: true, local_admin_enabled: true };
    session = undefined;
    expect(renderHook(() => useIsAdmin()).result.current).toBe(false);
  });
});
