import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { readThemePref, writeThemePref, applyTheme, useThemeSignal } from "./theme";

describe("theme preference (localStorage)", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    delete document.documentElement.dataset.theme;
  });

  it("defaults to dark when nothing stored", () => {
    expect(readThemePref()).toBe("dark");
  });

  it("round-trips a stored value", () => {
    writeThemePref("light");
    expect(readThemePref()).toBe("light");
  });

  it("ignores an invalid stored value and returns the default", () => {
    localStorage.setItem("transit.theme", "sepia");
    expect(readThemePref()).toBe("dark");
  });

  it("returns dark when localStorage.getItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("localStorage unavailable");
    });
    expect(readThemePref()).toBe("dark");
    spy.mockRestore();
  });

  it("doesn't throw when localStorage.setItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("localStorage unavailable");
    });
    expect(() => writeThemePref("light")).not.toThrow();
    spy.mockRestore();
  });

  it("applyTheme sets data-theme on the html element", () => {
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
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

  it("(b) initial value is the module default (dark) when data-theme is unset", () => {
    // data-theme unset (afterEach clears it). The old hand-rolled hook defaulted
    // to "light" here — contradicting the module's "dark" default; the
    // useSyncExternalStore rewrite falls back to DEFAULT_THEME consistently.
    const { result } = renderHook(() => useThemeSignal());
    expect(result.current).toBe("dark");
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
    // applyTheme's write-guard skips both the DOM write and the event dispatch
    // when the value is unchanged, so useSyncExternalStore never gets a
    // store-change notification -> no re-render of the consuming hook.
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    act(() => applyTheme("light"));
    expect(dispatchSpy).not.toHaveBeenCalled();
    expect(renders).toBe(before);
  });

  it("(e) a subsequent applyTheme after unmount does not throw or leak", () => {
    document.documentElement.dataset.theme = "light";
    const { unmount } = renderHook(() => useThemeSignal());
    unmount();
    // The subscribe cleanup removed the listener; a later toggle is a no-op for
    // this hook and must not throw (no reliable way to assert "did nothing"
    // beyond "no error" for an unmounted hook — sufficient here).
    expect(() => act(() => applyTheme("dark"))).not.toThrow();
  });
});
