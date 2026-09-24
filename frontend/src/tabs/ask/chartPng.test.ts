import { describe, it, expect, vi, afterEach } from "vitest";
import { exportSvgAsPng } from "./chartPng";

function makeSvg(): SVGSVGElement {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg") as SVGSVGElement;
  svg.setAttribute("width", "400");
  svg.setAttribute("height", "200");
  vi.spyOn(svg, "getBoundingClientRect").mockReturnValue({
    width: 400,
    height: 200,
  } as DOMRect);
  return svg;
}

describe("exportSvgAsPng", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("serializes the SVG, rasterizes it to a canvas, and triggers a PNG download", () => {
    const svg = makeSvg();
    const createObjectURL = vi.fn().mockReturnValue("blob:mock");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });

    const drawImage = vi.fn();
    const toBlob = vi.fn((cb: (b: Blob | null) => void) => cb(new Blob(["png"], { type: "image/png" })));
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ drawImage } as never);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(toBlob as never);

    let installedOnload: (() => void) | null = null;
    class FakeImage {
      set onload(fn: () => void) {
        installedOnload = fn;
      }
      set src(_v: string) {
        // Simulate the image finishing "loading" synchronously.
        installedOnload?.();
      }
    }
    vi.stubGlobal("Image", FakeImage);

    const clickSpy = vi.fn();
    const realCreateElement = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = realCreateElement(tag);
      if (tag === "a") el.click = clickSpy;
      return el;
    });

    exportSvgAsPng(svg, "chart.png");

    expect(drawImage).toHaveBeenCalled();
    expect(toBlob).toHaveBeenCalled();
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(createObjectURL).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalled();
  });

  it("does nothing (no throw) when canvas 2D context is unavailable", () => {
    const svg = makeSvg();
    vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn().mockReturnValue("blob:mock"), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    class FakeImage {
      onload: (() => void) | null = null;
      set src(_v: string) {
        this.onload?.();
      }
    }
    vi.stubGlobal("Image", FakeImage);
    expect(() => exportSvgAsPng(svg, "chart.png")).not.toThrow();
  });
});
