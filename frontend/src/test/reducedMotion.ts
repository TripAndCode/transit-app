import { vi } from "vitest";

/**
 * Makes `window.matchMedia` report `prefers-reduced-motion: reduce`, so a
 * figure driven by `useCountUp` is settled at its final value on the first
 * render instead of starting its entrance at 0.
 *
 * For a test whose subject is the figure rather than its arrival: the
 * count-up entrance itself is covered in `useCountUp.test.ts`, and here a
 * frame clock would only stand between the test and the value it asserts.
 * Call it from `beforeEach` and pair it with `vi.restoreAllMocks()`.
 */
export function stubReducedMotion(): void {
  vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
    matches: query.includes("prefers-reduced-motion"),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList);
}
