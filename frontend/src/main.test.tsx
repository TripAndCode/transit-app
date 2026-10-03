import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { LocaleUnavailable } from "./components/LocaleUnavailable";

const { localeReady, createRoot, renderRoot } = vi.hoisted(() => {
  let resolve!: (loaded: boolean) => void;
  const promise = new Promise<boolean>((r) => (resolve = r));
  const renderRoot = vi.fn();
  return {
    localeReady: { promise, resolve },
    createRoot: vi.fn(() => ({ render: renderRoot })),
    renderRoot,
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

    localeReady.resolve(true);
    await vi.waitFor(() => expect(createRoot).toHaveBeenCalledTimes(1));
    const [tree] = renderRoot.mock.calls[0] as [ReactElement<{ children: ReactElement }>];
    expect(tree.props.children.type).not.toBe(LocaleUnavailable);
  });
});
