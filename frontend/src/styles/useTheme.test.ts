import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useTheme } from "./useTheme";

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

describe("useTheme", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    delete document.documentElement.dataset.theme;
    vi.restoreAllMocks();
  });

  it("defaults to system and paints the OS-resolved theme on mount", () => {
    mockColorScheme(true);
    const { result } = renderHook(() => useTheme());
    expect(result.current[0]).toBe("system");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("initializes from a stored explicit preference, which stays authoritative", () => {
    mockColorScheme(true);
    localStorage.setItem("transit.theme", "light");
    const { result } = renderHook(() => useTheme());
    expect(result.current[0]).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("setter updates state, persists, and re-applies data-theme", () => {
    mockColorScheme(false);
    const { result } = renderHook(() => useTheme());
    act(() => result.current[1]("dark"));
    expect(result.current[0]).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("transit.theme")).toBe("dark");
  });

  it("follows a live OS appearance change while the preference is system", () => {
    const scheme = mockColorScheme(false);
    renderHook(() => useTheme());
    expect(document.documentElement.dataset.theme).toBe("light");
    act(() => scheme.set(true));
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("stops following the OS once an explicit preference is chosen", () => {
    const scheme = mockColorScheme(false);
    const { result } = renderHook(() => useTheme());
    act(() => result.current[1]("light"));
    expect(scheme.listenerCount()).toBe(0);
    act(() => scheme.set(true));
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("keeps every caller on one preference, so a choice made in one is seen by the others", () => {
    mockColorScheme(false);
    const palette = renderHook(() => useTheme());
    const menu = renderHook(() => useTheme());
    act(() => menu.result.current[1]("dark"));
    expect(menu.result.current[0]).toBe("dark");
    expect(palette.result.current[0]).toBe("dark");
  });

  it("does not let the OS repaint over an explicit choice made through another caller", () => {
    const scheme = mockColorScheme(false);
    renderHook(() => useTheme());
    const menu = renderHook(() => useTheme());
    act(() => menu.result.current[1]("dark"));
    act(() => scheme.set(false));
    act(() => scheme.set(true));
    act(() => scheme.set(false));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(scheme.listenerCount()).toBe(0);
  });

  it("holds a single OS listener however many callers follow the system theme", () => {
    const scheme = mockColorScheme(false);
    renderHook(() => useTheme());
    renderHook(() => useTheme());
    expect(scheme.listenerCount()).toBe(1);
  });

  it("keeps a choice for the session when storage refuses the write", () => {
    mockColorScheme(false);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    const first = renderHook(() => useTheme());
    const second = renderHook(() => useTheme());
    act(() => first.result.current[1]("dark"));
    expect(second.result.current[0]).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    // Storage working again, a later write is the record once more.
    vi.restoreAllMocks();
    act(() => first.result.current[1]("system"));
  });
});
