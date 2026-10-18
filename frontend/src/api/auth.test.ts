import { afterEach, describe, expect, it, vi } from "vitest";
import { loginUrl } from "./auth";

describe("loginUrl", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("starts sign-in on the API's origin, where the OAuth routes live", () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.test");
    expect(loginUrl("google", "/agencies/1")).toBe("https://api.example.test/api/auth/google/login?next=%2Fagencies%2F1");
  });
});
