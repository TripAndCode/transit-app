import { afterEach, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { useSvgTextMeasurer } from "./labelFit";

class FakeOffscreenCanvas {
  getContext() {
    return { font: "", measureText: (text: string) => ({ width: text.length }) };
  }
}

afterEach(() => {
  Reflect.deleteProperty(document, "fonts");
});

// The first test in this file to touch the module, so the font state is
// read from the stub below rather than from jsdom's missing FontFaceSet.
it("hands out a new measurer once the web fonts have loaded, re-rendering the chart", async () => {
  vi.stubGlobal("OffscreenCanvas", FakeOffscreenCanvas);
  let loaded!: () => void;
  const ready = new Promise<void>((resolve) => {
    loaded = resolve;
  });
  Object.defineProperty(document, "fonts", { value: { status: "loading", ready }, configurable: true });
  const seen: unknown[] = [];
  function Probe() {
    seen.push(useSvgTextMeasurer(14));
    return null;
  }
  render(<Probe />);
  const before = seen.at(-1);
  expect(before).toBeTypeOf("function");
  await act(async () => {
    loaded();
    await ready;
  });
  expect(seen.at(-1)).toBeTypeOf("function");
  expect(seen.at(-1)).not.toBe(before);
});
