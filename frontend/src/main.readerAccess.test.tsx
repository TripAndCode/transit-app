import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// main.tsx mounts once per module instance and builds its router from the
// URL at import time, so this outcome gets its own file.
const { createRoot, renderRoot } = vi.hoisted(() => {
  const renderRoot = vi.fn();
  return { createRoot: vi.fn(() => ({ render: renderRoot })), renderRoot };
});

vi.mock("./i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./i18n")>()),
  i18nReady: Promise.resolve(true),
}));
vi.mock("react-dom/client", () => ({ default: { createRoot }, createRoot }));
vi.mock("./App", () => ({ default: () => <p>app shell</p> }));
vi.mock("./api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api/auth")>()),
  useSession: () => ({ data: null, isError: false }),
}));
// Still in flight: nothing the shell renders may wait on the config.
vi.mock("./api/config", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api/config")>()),
  useConfig: () => ({ data: undefined, isError: false }),
}));

describe("a signed-out visitor on a reader route", () => {
  it("gets the app shell at that URL, with no redirect to sign-in or the welcome page", async () => {
    window.history.replaceState(null, "", "/agencies/1/pulse");
    await import("./main");
    await vi.waitFor(() => expect(renderRoot).toHaveBeenCalledTimes(1));
    const [tree] = renderRoot.mock.calls[0] as [ReactElement];
    render(tree);

    expect(await screen.findByText("app shell")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/agencies/1/pulse");
  });
});
