import { describe, expect, it, vi } from "vitest";

const { localeReady, createRoot } = vi.hoisted(() => {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => (resolve = r));
  const render = () => {};
  return {
    localeReady: { promise, resolve },
    createRoot: vi.fn(() => ({ render })),
  };
});

vi.mock("./i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./i18n")>()),
  i18nReady: localeReady.promise,
}));
vi.mock("react-dom/client", () => ({ default: { createRoot }, createRoot }));
// Only the mount order is under test; the shell's own module graph is not.
vi.mock("./App", () => ({ default: () => null }));

describe("app bootstrap", () => {
  it("mounts only after the active locale has loaded, so no key renders untranslated", async () => {
    await import("./main");
    await Promise.resolve();
    expect(createRoot).not.toHaveBeenCalled();

    localeReady.resolve();
    await vi.waitFor(() => expect(createRoot).toHaveBeenCalledTimes(1));
  });
});
