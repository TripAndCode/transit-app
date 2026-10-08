import { describe, it, expect, afterEach } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import type { ReactNode } from "react";
import { writeLastAgency, clearLastAgency } from "./lastAgency";
import { useRailAgencyId } from "./railAgency";

function setup(at: string) {
  let router!: ReturnType<typeof createMemoryRouter>;
  const hook = renderHook(() => useRailAgencyId(), {
    wrapper: ({ children }: { children: ReactNode }) => {
      router ??= createMemoryRouter(
        [
          { path: "/", element: children },
          { path: "/help", element: children },
          { path: "/agencies/:agencyId/*", element: children },
        ],
        { initialEntries: [at] },
      );
      return <RouterProvider router={router} />;
    },
  });
  return { ...hook, go: (to: string) => act(() => void router.navigate(to)) };
}

describe("useRailAgencyId", () => {
  afterEach(() => {
    cleanup();
    clearLastAgency();
  });

  it("is the agency in the URL", () => {
    expect(setup("/agencies/8/time").result.current).toBe("8");
  });

  it("keeps the agency just left on a screen outside any agency, such as Help", () => {
    const { result, go } = setup("/agencies/8/time");
    go("/help");
    expect(result.current).toBe("8");
  });

  it("falls back to the last chosen agency when Help is opened first", () => {
    writeLastAgency(3);
    expect(setup("/help").result.current).toBe("3");
  });

  it("offers none on the agency picker, where choosing one is the point", () => {
    writeLastAgency(3);
    const { result, go } = setup("/agencies/8/time");
    go("/");
    expect(result.current).toBeUndefined();
  });
});
