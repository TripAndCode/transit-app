import "@testing-library/jest-dom/vitest";
import { afterEach, beforeAll } from "vitest";
import { cleanup } from "@testing-library/react";
import i18n from "../i18n";

// Ensure React Testing Library unmounts components and clears the DOM between
// tests so state never leaks across cases.
afterEach(() => {
  cleanup();
});

// i18next's LanguageDetector reads navigator.language at init, so a test's
// starting locale otherwise depends on the environment running it rather
// than the fixture data it renders. Pin every test file to English up front;
// a test that specifically exercises the Japanese strings still opts in with
// its own `i18n.changeLanguage("ja")`.
beforeAll(async () => {
  await i18n.changeLanguage("en");
});

// jsdom doesn't implement window.matchMedia. Components (e.g. ThreadSidebar's
// mobile/desktop split) that read it to conditionally render need a stub, or
// every render throws "matchMedia is not a function". Defaults to a
// non-matching MediaQueryList so viewport-narrow queries resolve to desktop
// behavior unless a test explicitly overrides it. Pure-logic test files that
// opt into the cheaper `node` environment (no `window` at all) skip this.
if (typeof window !== "undefined" && !window.matchMedia) {
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

// jsdom doesn't implement Element.scrollTo (used by components that keep a
// scroll container pinned to the top on new content, e.g. AskTab's message
// list) — without a stub, mounting such a component throws "scrollTo is not
// a function" for every test, not just ones about scroll position.
if (typeof Element !== "undefined" && !Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {};
}

// jsdom implements neither IntersectionObserver nor ResizeObserver. These
// defaults are inert (they never call back) so a component that observes an
// element without checking for the class first mounts without throwing
// instead of leaving the observed state permanently at its initial value. A
// test that needs the observer to actually fire installs its own driveable
// stub with `vi.stubGlobal` (see RevealSection.test.tsx, useInView.test.ts),
// which overrides this default for that test and is restored by
// `vi.unstubAllGlobals()` afterward. A test that specifically depends on
// `IntersectionObserver`/`ResizeObserver` being absent (the real jsdom
// default) forces that with `vi.stubGlobal(name, undefined)` instead of
// relying on the ambient environment. Pure-logic test files that opt into
// the cheaper `node` environment (no `window` at all) skip this, same as
// the matchMedia/scrollTo stubs above.
if (typeof window !== "undefined" && typeof IntersectionObserver === "undefined") {
  class NoopIntersectionObserver {
    root: Element | Document | null = null;
    rootMargin = "";
    thresholds: ReadonlyArray<number> = [];
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
    takeRecords(): IntersectionObserverEntry[] {
      return [];
    }
  }
  window.IntersectionObserver = NoopIntersectionObserver as unknown as typeof IntersectionObserver;
}
if (typeof window !== "undefined" && typeof ResizeObserver === "undefined") {
  class NoopResizeObserver {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  window.ResizeObserver = NoopResizeObserver as unknown as typeof ResizeObserver;
}
