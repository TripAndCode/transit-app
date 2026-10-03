import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { design } from "./i18n/design";

// main.tsx mounts once per module instance, so the unavailable outcome gets
// its own file rather than sharing main.test.tsx's import.
const { createRoot, renderRoot } = vi.hoisted(() => {
  const renderRoot = vi.fn();
  return { createRoot: vi.fn(() => ({ render: renderRoot })), renderRoot };
});

vi.mock("./i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./i18n")>()),
  i18nReady: Promise.resolve(false),
}));
vi.mock("react-dom/client", () => ({ default: { createRoot }, createRoot }));
vi.mock("./App", () => ({ default: () => <p>app shell</p> }));

describe("app bootstrap when the active language can't load", () => {
  it("mounts a reload notice instead of an app full of bare keys", async () => {
    await import("./main");
    await vi.waitFor(() => expect(renderRoot).toHaveBeenCalledTimes(1));
    const [tree] = renderRoot.mock.calls[0] as [ReactElement];
    render(tree);

    expect(screen.getByRole("alert").textContent).toContain(design.en.stringsUnavailable);
    expect(screen.getByRole("button", { name: design.en.reload })).toBeTruthy();
    expect(screen.queryByText("app shell")).toBeNull();
  });
});
