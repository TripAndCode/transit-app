import { describe, it, expect, vi, afterEach } from "vitest";
import { useRef } from "react";
import { act, render } from "@testing-library/react";
import { useFlipRows } from "./useFlipRows";

function List({ order, keyOf }: { order: string[]; keyOf: string }) {
  const ref = useRef<HTMLDivElement | null>(null);
  useFlipRows(ref, keyOf);
  return (
    <div ref={ref}>
      {order.map((id) => (
        <div key={id} data-flip-key={id}>
          {id}
        </div>
      ))}
    </div>
  );
}

/** jsdom lays nothing out, so every rect is 0 -- stub `top` per row id. */
function stubTops(tops: Record<string, number>) {
  return vi
    .spyOn(HTMLElement.prototype, "getBoundingClientRect")
    .mockImplementation(function (this: HTMLElement) {
      const id = this.dataset.flipKey ?? "";
      return { top: tops[id] ?? 0 } as DOMRect;
    });
}

afterEach(() => vi.restoreAllMocks());

describe("useFlipRows", () => {
  it("does nothing on the first render -- there is no previous position to come from", () => {
    stubTops({ a: 0, b: 40 });
    const { container } = render(<List order={["a", "b"]} keyOf="1" />);
    for (const row of container.querySelectorAll<HTMLElement>("[data-flip-key]")) {
      expect(row.style.transform).toBe("");
    }
  });

  it("inverts a row's movement, then releases it on the next frame", () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      frames.push(cb);
      return frames.length;
    });
    const rect = stubTops({ a: 0, b: 40 });
    const { container, rerender } = render(<List order={["a", "b"]} keyOf="1" />);

    // a and b swap places: each moves 40px in the opposite direction.
    rect.mockImplementation(function (this: HTMLElement) {
      return { top: { b: 0, a: 40 }[this.dataset.flipKey ?? ""] ?? 0 } as DOMRect;
    });
    rerender(<List order={["b", "a"]} keyOf="2" />);

    const row = (id: string) => container.querySelector<HTMLElement>(`[data-flip-key="${id}"]`)!;
    expect(row("a").style.transform).toBe("translateY(-40px)");
    expect(row("b").style.transform).toBe("translateY(40px)");
    expect(row("a").style.transition).toBe("none");

    act(() => frames.forEach((f) => f(0)));
    expect(row("a").style.transform).toBe("");
    expect(row("a").style.transition).toBe("");
  });

  it("leaves rows alone under reduced motion", () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({ matches: true } as MediaQueryList);
    const rect = stubTops({ a: 0, b: 40 });
    const { container, rerender } = render(<List order={["a", "b"]} keyOf="1" />);
    rect.mockImplementation(function (this: HTMLElement) {
      return { top: { b: 0, a: 40 }[this.dataset.flipKey ?? ""] ?? 0 } as DOMRect;
    });
    rerender(<List order={["b", "a"]} keyOf="2" />);
    expect(container.querySelector<HTMLElement>('[data-flip-key="a"]')!.style.transform).toBe("");
  });

  it("does not move a row that stayed put", () => {
    stubTops({ a: 0, b: 40 });
    const { container, rerender } = render(<List order={["a", "b"]} keyOf="1" />);
    rerender(<List order={["a", "b"]} keyOf="2" />);
    expect(container.querySelector<HTMLElement>('[data-flip-key="b"]')!.style.transform).toBe("");
  });

  it("measures every row before offsetting any, so a long re-sort forces one style pass, not one per row", () => {
    vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);
    const rect = stubTops({ a: 0, b: 40, c: 80 });
    const { container, rerender } = render(<List order={["a", "b", "c"]} keyOf="1" />);
    let readAfterWrite = false;
    rect.mockImplementation(function (this: HTMLElement) {
      const rows = Array.from(container.querySelectorAll<HTMLElement>("[data-flip-key]"));
      if (this.dataset.flipKey != null && rows.some((row) => row.style.transform !== "")) readAfterWrite = true;
      const id = this.dataset.flipKey ?? "";
      return { top: { c: 0, b: 40, a: 80 }[id] ?? 0 } as DOMRect;
    });
    rerender(<List order={["c", "b", "a"]} keyOf="2" />);
    expect(container.querySelector<HTMLElement>('[data-flip-key="a"]')!.style.transform).toBe("translateY(-80px)");
    expect(readAfterWrite).toBe(false);
  });

  it("flushes style once after offsetting, so a re-sort committed outside an input event still plays", () => {
    vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);
    const rect = stubTops({ a: 0, b: 40, c: 80 });
    const { container, rerender } = render(<List order={["a", "b", "c"]} keyOf="1" />);
    let flushesAfterWrite = 0;
    rect.mockImplementation(function (this: HTMLElement) {
      const rows = Array.from(container.querySelectorAll<HTMLElement>("[data-flip-key]"));
      if (rows.some((row) => row.style.transform !== "")) flushesAfterWrite++;
      return { top: { c: 0, b: 40, a: 80 }[this.dataset.flipKey ?? ""] ?? 0 } as DOMRect;
    });
    rerender(<List order={["c", "b", "a"]} keyOf="2" />);
    expect(flushesAfterWrite).toBe(1);
  });

  it("measures rows against the list, so a scroll between re-sorts leaves rows that kept their place alone", () => {
    vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);
    let scroll = 0;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const key = this.dataset.flipKey;
      const top = key == null ? 90 : { a: 100, b: 140 }[key] ?? 0;
      return { top: top - scroll } as DOMRect;
    });
    const { container, rerender } = render(<List order={["a", "b"]} keyOf="1" />);
    scroll = 50;
    rerender(<List order={["a", "b"]} keyOf="2" />);
    for (const row of container.querySelectorAll<HTMLElement>("[data-flip-key]")) expect(row.style.transform).toBe("");
  });
});
