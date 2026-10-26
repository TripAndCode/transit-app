import { describe, it, expect, vi, afterEach } from "vitest";
import { render, act } from "@testing-library/react";
import { useElementWidth } from "./useElementWidth";

type ResizeCallback = (entries: { contentRect: { width: number } }[]) => void;

/** A ResizeObserver whose callback the test fires by hand: jsdom lays nothing
 *  out, so there is no real resize to wait for. */
function mockResizeObserver() {
  let callback: ResizeCallback = () => {};
  const observe = vi.fn();
  const disconnect = vi.fn();
  class Stub {
    constructor(cb: ResizeCallback) {
      callback = cb;
    }
    observe = observe;
    unobserve = vi.fn();
    disconnect = disconnect;
  }
  vi.stubGlobal("ResizeObserver", Stub);
  return {
    resize(width: number) {
      act(() => callback([{ contentRect: { width } }]));
    },
    observe,
    disconnect,
  };
}

function Probe() {
  const [ref, width] = useElementWidth<HTMLDivElement>();
  return (
    <div ref={ref} data-testid="box">
      {width ?? "unmeasured"}
    </div>
  );
}

describe("useElementWidth", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("stays unmeasured while the element has no layout width", () => {
    mockResizeObserver();
    const { getByTestId } = render(<Probe />);
    expect(getByTestId("box").textContent).toBe("unmeasured");
  });

  it("reads the element's width as soon as it mounts, before any resize", () => {
    mockResizeObserver();
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ width: 358 } as DOMRect);
    const { getByTestId } = render(<Probe />);
    expect(getByTestId("box").textContent).toBe("358");
  });

  it("follows the element as it resizes", () => {
    const observer = mockResizeObserver();
    const { getByTestId } = render(<Probe />);
    expect(observer.observe).toHaveBeenCalledWith(getByTestId("box"));
    observer.resize(572);
    expect(getByTestId("box").textContent).toBe("572");
    observer.resize(358);
    expect(getByTestId("box").textContent).toBe("358");
  });

  it("keeps the last width when the element collapses to zero, as a hidden panel does", () => {
    const observer = mockResizeObserver();
    const { getByTestId } = render(<Probe />);
    observer.resize(572);
    observer.resize(0);
    expect(getByTestId("box").textContent).toBe("572");
  });

  it("stops observing once the element unmounts", () => {
    const observer = mockResizeObserver();
    const { unmount } = render(<Probe />);
    unmount();
    expect(observer.disconnect).toHaveBeenCalled();
  });

  it("keeps the width it first read where there is no ResizeObserver", () => {
    vi.stubGlobal("ResizeObserver", undefined);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ width: 358 } as DOMRect);
    const { getByTestId } = render(<Probe />);
    expect(getByTestId("box").textContent).toBe("358");
  });
});
