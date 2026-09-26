import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  readThemePref,
  writeThemePref,
  applyTheme,
  resolveTheme,
  subscribeSystemTheme,
  useThemeSignal,
} from "./theme";

/** Replace window.matchMedia with a controllable prefers-color-scheme stub.
 *  Returns a setter that flips the match and notifies every listener, the way
 *  a real OS appearance change does. */
function mockColorScheme(prefersDark: boolean) {
  const listeners = new Set<(e: MediaQueryListEvent) => void>();
  let matches = prefersDark;
  const mql = {
    get matches() {
      return matches;
    },
    media: "(prefers-color-scheme: dark)",
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: (_: string, cb: (e: MediaQueryListEvent) => void) => listeners.add(cb),
    removeEventListener: (_: string, cb: (e: MediaQueryListEvent) => void) => listeners.delete(cb),
    dispatchEvent: () => false,
  } as unknown as MediaQueryList;
  vi.spyOn(window, "matchMedia").mockImplementation(() => mql);
  return {
    listenerCount: () => listeners.size,
    set(next: boolean) {
      matches = next;
      for (const cb of [...listeners]) cb({ matches: next } as MediaQueryListEvent);
    },
  };
}

describe("theme preference (localStorage)", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    delete document.documentElement.dataset.theme;
    vi.restoreAllMocks();
  });

  it("defaults to system when nothing stored", () => {
    expect(readThemePref()).toBe("system");
  });

  it("round-trips every stored value, including an explicit system", () => {
    for (const theme of ["light", "dark", "system"] as const) {
      writeThemePref(theme);
      expect(readThemePref()).toBe(theme);
    }
  });

  it("ignores an invalid stored value and returns the default", () => {
    localStorage.setItem("transit.theme", "sepia");
    expect(readThemePref()).toBe("system");
  });

  it("returns system when localStorage.getItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("localStorage unavailable");
    });
    expect(readThemePref()).toBe("system");
    spy.mockRestore();
  });

  it("doesn't throw when localStorage.setItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("localStorage unavailable");
    });
    expect(() => writeThemePref("system")).not.toThrow();
    spy.mockRestore();
  });
});

describe("resolveTheme", () => {
  afterEach(() => vi.restoreAllMocks());

  it("passes an explicit preference straight through", () => {
    mockColorScheme(true);
    expect(resolveTheme("light")).toBe("light");
    expect(resolveTheme("dark")).toBe("dark");
  });

  it("reads prefers-color-scheme for system", () => {
    const scheme = mockColorScheme(true);
    expect(resolveTheme("system")).toBe("dark");
    scheme.set(false);
    expect(resolveTheme("system")).toBe("light");
  });
});

describe("applyTheme", () => {
  afterEach(() => {
    delete document.documentElement.dataset.theme;
    vi.restoreAllMocks();
  });

  it("sets data-theme to the explicit preference", () => {
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("writes the RESOLVED theme for system, never the literal 'system'", () => {
    // data-theme is what global.css's `:root[data-theme="dark"]` selects on,
    // so it only ever holds a concrete theme.
    mockColorScheme(true);
    applyTheme("system");
    expect(document.documentElement.dataset.theme).toBe("dark");
    mockColorScheme(false);
    applyTheme("system");
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});

describe("subscribeSystemTheme", () => {
  afterEach(() => vi.restoreAllMocks());

  it("fires on an OS appearance change and unsubscribes cleanly", () => {
    const scheme = mockColorScheme(false);
    const onChange = vi.fn();
    const unsubscribe = subscribeSystemTheme(onChange);
    expect(scheme.listenerCount()).toBe(1);
    scheme.set(true);
    expect(onChange).toHaveBeenCalledOnce();
    unsubscribe();
    expect(scheme.listenerCount()).toBe(0);
    scheme.set(false);
    expect(onChange).toHaveBeenCalledOnce();
  });
});

describe("useThemeSignal (useSyncExternalStore)", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    delete document.documentElement.dataset.theme;
    vi.restoreAllMocks();
  });

  it("(a) initial value reflects data-theme when it is set", () => {
    document.documentElement.dataset.theme = "light";
    const { result } = renderHook(() => useThemeSignal());
    expect(result.current).toBe("light");
  });

  it("(b) initial value is the resolved default (light) when data-theme is unset", () => {
    const { result } = renderHook(() => useThemeSignal());
    expect(result.current).toBe("light");
  });

  it("(c) updates the returned value when applyTheme sets a new theme", () => {
    document.documentElement.dataset.theme = "light";
    const { result } = renderHook(() => useThemeSignal());
    expect(result.current).toBe("light");
    act(() => applyTheme("dark"));
    expect(result.current).toBe("dark");
  });

  it("(d) does not fire a spurious update when applyTheme re-applies the current theme", () => {
    document.documentElement.dataset.theme = "light";
    let renders = 0;
    renderHook(() => {
      renders += 1;
      return useThemeSignal();
    });
    const before = renders;
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    act(() => applyTheme("light"));
    expect(dispatchSpy).not.toHaveBeenCalled();
    expect(renders).toBe(before);
  });

  it("(e) a subsequent applyTheme after unmount does not throw or leak", () => {
    document.documentElement.dataset.theme = "light";
    const { unmount } = renderHook(() => useThemeSignal());
    unmount();
    expect(() => act(() => applyTheme("dark"))).not.toThrow();
  });
});

describe("index.html pre-mount script", () => {
  // The inline script paints data-theme before React mounts, so it has to
  // reproduce readThemePref/resolveTheme exactly; if it and theme.ts disagree
  // the page flashes the wrong theme on every load.
  const html = readFileSync(resolve(process.cwd(), "index.html"), "utf8");

  it("reads the same storage key and understands 'system'", () => {
    expect(html).toContain("transit.theme");
    expect(html).toContain("system");
  });

  it("resolves system through prefers-color-scheme", () => {
    expect(html).toContain("(prefers-color-scheme: dark)");
  });

  it("never writes the literal 'system' into data-theme", () => {
    const assignments = [...html.matchAll(/dataset\.theme\s*=\s*([^;]+);/g)].map((m) => m[1].trim());
    expect(assignments.length).toBeGreaterThan(0);
    for (const value of assignments) expect(value).not.toBe('"system"');
  });
});
