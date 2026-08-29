import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Ensure React Testing Library unmounts components and clears the DOM between
// tests so state never leaks across cases.
afterEach(() => {
  cleanup();
});

// jsdom doesn't implement window.matchMedia. Components (e.g. ThreadSidebar's
// mobile/desktop split) that read it to conditionally render need a stub, or
// every render throws "matchMedia is not a function". Defaults to a
// non-matching MediaQueryList so viewport-narrow queries resolve to desktop
// behavior unless a test explicitly overrides it.
if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList;
}
