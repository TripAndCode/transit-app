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
      const id = this.dataset.flipKey ?? "";
      return { top: id === "b" ? 0 : 40 } as DOMRect;
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
      const id = this.dataset.flipKey ?? "";
      return { top: id === "b" ? 0 : 40 } as DOMRect;
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
});
